"""云资源同步消费处理器。

消费 Kafka Topic: cloud-sync-{provider}，将云资源变更同步到 CMDB。

处理逻辑：
1. 幂等校验（resource_version）
2. Upsert / 软删除资源
3. 同步云标签（source='cloud'，不覆盖手动标签）
4. 重建从属 + 关联关系
5. 记录变更审计
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bingops.models.cmdb.change_log import CmdbChangeLog
from bingops.models.cmdb.resource import CmdbResource
from bingops.repositories.cmdb.change_log_repo import CmdbChangeLogRepo
from bingops.repositories.cmdb.resource_repo import CmdbResourceRepo
from bingops.repositories.cmdb.sync_task_repo import CmdbSyncTaskRepo
from bingops.schemas.cmdb.kafka_messages import CloudResourceMessage, CloudSyncEventType
from bingops.tasks.cmdb.relationship_builder import rebuild_cloud_relationships

logger = logging.getLogger(f"bingops.{__name__}")


def create_cloud_handler(session_factory: async_sessionmaker[AsyncSession]):
    """创建云资源同步处理函数（闭包注入 session_factory）。"""

    async def handle_cloud_sync(message: CloudResourceMessage) -> None:
        """处理单条云资源同步消息。"""
        async with session_factory() as session:
            try:
                # 同步任务开关判断
                sync_repo = CmdbSyncTaskRepo(session)
                if not await sync_repo.is_enabled("cloud", message.cloud_account):
                    logger.debug("Cloud sync disabled for account, skipping", extra={"cloud_account": message.cloud_account})
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
    """Upsert 云资源（幂等）。"""
    repo = CmdbResourceRepo(session)
    existing = await repo.get_by_provider_id(
        message.provider, message.resource_type, message.provider_id, message.cloud_account,
    )

    # 幂等校验
    if existing and existing.resource_version:
        if _version_lte(message.resource_version, existing.resource_version):
            logger.debug("Cloud sync event skipped (version not newer)", extra={"provider_id": message.provider_id})
            return

    if existing:
        # 更新
        existing.name = message.name
        existing.region = message.region
        existing.zone = message.zone
        existing.status = message.status
        existing.attributes = message.attributes
        existing.resource_version = message.resource_version
        existing.synced_at = datetime.now(timezone.utc)
        existing.source = "discovery"
        existing.deleted_at = None  # 恢复软删除
        await repo.update(existing)
        resource = existing

        await _record_change(session, resource.id, message.resource_type, "update", source="kafka")
        logger.info("Cloud resource updated", extra={"provider_id": message.provider_id})
    else:
        # 新建
        resource = CmdbResource(
            provider=message.provider,
            resource_type=message.resource_type,
            provider_id=message.provider_id,
            cloud_account=message.cloud_account,
            name=message.name,
            region=message.region,
            zone=message.zone,
            status=message.status,
            attributes=message.attributes,
            resource_version=message.resource_version,
            synced_at=datetime.now(timezone.utc),
            source="discovery",
        )
        resource = await repo.create(resource)

        await _record_change(session, resource.id, message.resource_type, "create", source="kafka")
        logger.info("Cloud resource created", extra={"provider_id": message.provider_id})

    # 同步云标签（source='cloud'，不覆盖手动标签）
    await _sync_cloud_tags(session, resource, message.cloud_tags)

    # 重建关系
    await rebuild_cloud_relationships(session, resource, message)


async def _handle_delete(session: AsyncSession, message: CloudResourceMessage) -> None:
    """软删除云资源。"""
    repo = CmdbResourceRepo(session)
    existing = await repo.get_by_provider_id(
        message.provider, message.resource_type, message.provider_id, message.cloud_account,
    )
    if existing is None:
        logger.debug("Cloud resource not found for delete, skipping", extra={"provider_id": message.provider_id})
        return

    await repo.soft_delete(existing)
    await _record_change(session, existing.id, message.resource_type, "delete", source="kafka")
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
    resource_type: str,
    change_type: str,
    source: str = "kafka",
) -> None:
    """记录变更日志。"""
    log_repo = CmdbChangeLogRepo(session)
    log = CmdbChangeLog(
        resource_id=resource_id,
        resource_type=resource_type,
        change_type=change_type,
        source=source,
    )
    await log_repo.create(log)


def _version_lte(v1: str, v2: str) -> bool:
    """比较版本号。"""
    try:
        return int(v1) <= int(v2)
    except (ValueError, TypeError):
        return v1 <= v2
