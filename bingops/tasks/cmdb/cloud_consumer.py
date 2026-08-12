"""云资源同步消费处理器（过渡态）。

消费 Kafka Topic: cloud-sync-{provider}，将云资源变更同步到 CMDB。

注意：v2 表结构已改为 model_id + fields JSONB，本消费器的字段映射仍是
v1 硬编码风格，仅保证可运行不报错；待附录 B #16 云链路段重写为模型定义驱动。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bingops.models.cmdb.change_log import CmdbChangeLog
from bingops.models.cmdb.resource import CmdbResource
from bingops.repositories.cmdb.change_log_repo import CmdbChangeLogRepo
from bingops.repositories.cmdb.model_repo import CmdbModelRepo
from bingops.repositories.cmdb.resource_repo import CmdbResourceRepo
from bingops.repositories.cmdb.sync_task_repo import CmdbSyncTaskRepo
from bingops.schemas.cmdb.kafka_messages import CloudResourceMessage, CloudSyncEventType

logger = logging.getLogger(f"bingops.{__name__}")


def create_cloud_handler(session_factory: async_sessionmaker[AsyncSession]):
    """创建云资源同步处理函数（闭包注入 session_factory）。"""

    async def handle_cloud_sync(message: CloudResourceMessage) -> None:
        """处理单条云资源同步消息。"""
        async with session_factory() as session:
            try:
                # 同步任务门控：数据表驱动，未配置或禁用 → 跳过（默认拒绝）
                sync_repo = CmdbSyncTaskRepo(session)
                task = await sync_repo.get_by_type_and_target("cloud", message.cloud_account)
                if task is None or not task.enabled:
                    logger.debug(
                        "Cloud sync task not configured or disabled, skipping",
                        extra={"cloud_account": message.cloud_account},
                    )
                    return
                # resource_types 白名单过滤（空列表 = 同步全部类型）
                if task.resource_types and message.resource_type not in task.resource_types:
                    logger.debug(
                        "Cloud resource type not in sync task, skipping",
                        extra={"cloud_account": message.cloud_account, "resource_type": message.resource_type},
                    )
                    return

                if message.event_type == CloudSyncEventType.DELETE:
                    await _handle_delete(session, message)
                else:
                    await _handle_upsert(session, message)

                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception(
                    "Failed to process cloud sync event",
                    extra={
                        "provider": message.provider,
                        "resource_type": message.resource_type,
                        "provider_id": message.provider_id,
                    },
                )

    return handle_cloud_sync


async def _handle_upsert(session: AsyncSession, message: CloudResourceMessage) -> None:
    """Upsert 云资源（过渡态：resource_type → 同名模型 code 直映射）。"""
    repo = CmdbResourceRepo(session)
    model_repo = CmdbModelRepo(session)
    model = await model_repo.get_model_by_code(message.resource_type)
    if model is None:
        logger.warning(
            "Cloud resource model not defined, skipping",
            extra={"resource_type": message.resource_type, "provider_id": message.provider_id},
        )
        return

    existing = await repo.get_by_provider_id(
        model.id, message.provider, message.provider_id, message.cloud_account,
    )

    # 幂等校验：云 resource_version 是内容哈希（无序），仅当哈希相同（无实质变更）时跳过
    if existing and existing.resource_version == message.resource_version:
        logger.debug("Cloud sync event skipped (no content change)", extra={"provider_id": message.provider_id})
        return

    if existing:
        # 更新
        existing.name = message.name
        existing.region = message.region
        existing.zone = message.zone
        existing.status = message.status
        existing.fields = message.attributes
        existing.resource_version = message.resource_version
        existing.synced_at = datetime.now(timezone.utc)
        existing.source = "discovery"
        existing.deleted_at = None  # 恢复软删除
        await repo.update(existing)
        resource = existing

        await _record_change(session, resource.id, model.id, "update")
        logger.info("Cloud resource updated", extra={"provider_id": message.provider_id})
    else:
        # 新建
        resource = CmdbResource(
            model_id=model.id,
            provider=message.provider,
            provider_id=message.provider_id,
            cloud_account=message.cloud_account,
            name=message.name,
            region=message.region,
            zone=message.zone,
            status=message.status,
            fields=message.attributes,
            resource_version=message.resource_version,
            synced_at=datetime.now(timezone.utc),
            source="discovery",
        )
        resource = await repo.create(resource)

        await _record_change(session, resource.id, model.id, "create")
        logger.info("Cloud resource created", extra={"provider_id": message.provider_id})

    # 同步云标签（source='cloud'，不覆盖手动标签）
    await _sync_cloud_tags(session, resource, message.cloud_tags)


async def _handle_delete(session: AsyncSession, message: CloudResourceMessage) -> None:
    """软删除云资源。"""
    repo = CmdbResourceRepo(session)
    model_repo = CmdbModelRepo(session)
    model = await model_repo.get_model_by_code(message.resource_type)
    if model is None:
        return
    existing = await repo.get_by_provider_id(
        model.id, message.provider, message.provider_id, message.cloud_account,
    )
    if existing is None:
        logger.debug("Cloud resource not found for delete, skipping", extra={"provider_id": message.provider_id})
        return

    await repo.soft_delete(existing)
    await _record_change(session, existing.id, model.id, "delete")
    logger.info("Cloud resource soft-deleted", extra={"provider_id": message.provider_id})


async def _sync_cloud_tags(
    session: AsyncSession, resource: CmdbResource, cloud_tags: dict[str, str],
) -> None:
    """同步云标签到资源标签表。

    规则：
    - source='cloud' 的标签会被覆盖（云 API 是权威来源）
    - source='manual' 的标签不受影响（手动优先）
    """
    from bingops.models.cmdb.tag import CmdbResourceTag
    from bingops.repositories.cmdb.tag_repo import CmdbTagRepo

    if not cloud_tags:
        return

    tag_repo = CmdbTagRepo(session)
    now = datetime.now(timezone.utc)

    for raw_key, value in cloud_tags.items():
        # 归一化：统一转小写
        normalized_key = raw_key.lower()
        tag = CmdbResourceTag(
            resource_id=resource.id,
            tag_key=normalized_key,
            tag_value=value,
            source="cloud",
            raw_key=raw_key,
            synced_at=now,
        )
        await tag_repo.add_resource_tag(tag)


async def _record_change(
    session: AsyncSession,
    resource_id: int,
    model_id: int,
    change_type: str,
) -> None:
    """记录变更日志。"""
    log_repo = CmdbChangeLogRepo(session)
    log = CmdbChangeLog(
        resource_id=resource_id,
        model_id=model_id,
        change_type=change_type,
        source="discovery",
    )
    await log_repo.create(log)
