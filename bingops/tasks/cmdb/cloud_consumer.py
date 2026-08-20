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
from bingops.tasks.cmdb.cloud_relationship_builder import rebuild_cloud_relationships
from bingops.tasks.cmdb.relationship_builder import remove_resource_edges

logger = logging.getLogger(f"bingops.{__name__}")


def create_cloud_handler(session_factory: async_sessionmaker[AsyncSession]):
    """创建云资源同步处理函数（闭包注入 session_factory）。"""

    async def handle_cloud_sync(message: CloudResourceMessage) -> None:
        """处理单条云资源同步消息。"""
        async with session_factory() as session:
            try:
                # 同步任务门控：数据表驱动，默认拒绝。
                # 同一账号可拆分多个任务（按资源类型独立调度），任一启用任务命中即放行
                sync_repo = CmdbSyncTaskRepo(session)
                tasks = await sync_repo.list_by_type_and_target("cloud", message.cloud_account)
                active_tasks = [t for t in tasks if t.enabled]
                if not active_tasks:
                    logger.debug(
                        "Cloud sync task not configured or disabled, skipping",
                        extra={"cloud_account": message.cloud_account},
                    )
                    return
                # resource_types 白名单过滤（空列表 = 同步全部类型）
                if not any(
                    not t.resource_types or message.resource_type in t.resource_types
                    for t in active_tasks
                ):
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


# ── 字段名兼容映射 ────────────────────────────────────────────────────────────

# 采集器 v1 → v2 字段名变更（aliyun_ecs）
_ALIYUN_ECS_FIELD_ALIASES: dict[str, str] = {
    "instance_type": "instance_class",
    "os_name": "os",
    "memory_mb": "memory_gb",
    "expired_time": "expired_at",
    "instance_charge_type": "charge_type",
}


def _normalize_attributes(resource_type: str, attributes: dict) -> dict:
    """归一化 attributes 字段名，兼容采集器 v1/v2 差异。

    采集器 v1 使用 instance_type/os_name 等内部命名，v2 已对齐 CMDB 模型 code。
    此函数将旧字段名映射到新字段名，确保消费端无论收到哪种格式都能正确入库。
    """
    if resource_type == "aliyun_ecs":
        for old_key, new_key in _ALIYUN_ECS_FIELD_ALIASES.items():
            if old_key in attributes and new_key not in attributes:
                attributes[new_key] = attributes.pop(old_key)
    return attributes


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
        include_deleted=True,
    )

    # 归一化 attributes 字段名，兼容采集器 v1/v2 差异
    attributes = _normalize_attributes(message.resource_type, message.attributes)

    # 幂等校验：云 resource_version 是内容哈希（无序），仅当哈希相同（无实质变更）时跳过；
    # 软删记录不跳过（需走更新分支复活）
    if existing and existing.deleted_at is None and existing.resource_version == message.resource_version:
        logger.debug("Cloud sync unchanged, skip upsert but rebuild relationships",
                     extra={"provider_id": message.provider_id})
        # 资源内容不变，但关系边可能因对端资源入库而需要补建
        await rebuild_cloud_relationships(session, existing, message)
        return

    if existing:
        # 更新
        existing.name = message.name
        existing.region = message.region
        existing.zone = message.zone
        existing.status = message.status
        existing.fields = attributes
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
            fields=attributes,
            resource_version=message.resource_version,
            synced_at=datetime.now(timezone.utc),
            source="discovery",
        )
        resource = await repo.create(resource)

        await _record_change(session, resource.id, model.id, "create")
        logger.info("Cloud resource created", extra={"provider_id": message.provider_id})

    # 同步云标签（source='cloud'，不覆盖手动标签）
    await _sync_cloud_tags(session, resource, message.cloud_tags)

    # 应用关联物化（#13）：按 app 标签重算 tag 派生关联
    from bingops.services.cmdb import business_app_service
    await business_app_service.refresh_app_links_from_tags(session, resource)

    # 重建云资源关系（ECS→VPC/VSwitch/SG 等）
    await rebuild_cloud_relationships(session, resource, message)


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
    await remove_resource_edges(session, existing.id)
    await _record_change(session, existing.id, model.id, "delete")
    logger.info("Cloud resource soft-deleted", extra={"provider_id": message.provider_id})


async def _sync_cloud_tags(
    session: AsyncSession, resource: CmdbResource, cloud_tags: dict[str, str],
) -> None:
    """差异同步云标签到资源标签表（source='cloud'）。

    规则：
    - 新增 / 改值 / 清除失效标签（云 API 是权威来源，空 dict = 清空云标签）
    - source='manual' 的标签不受影响（手动优先）
    - 增/删/改各记一条 change_type='tag' 审计（云上标签变更也是变更）
    """
    from bingops.models.cmdb.tag import CmdbResourceTag
    from bingops.repositories.cmdb.tag_repo import CmdbTagRepo

    tag_repo = CmdbTagRepo(session)
    now = datetime.now(timezone.utc)
    # 归一化：统一转小写，raw_key 保留原始大小写
    incoming = {
        (raw_key or "").lower(): (raw_key, value)
        for raw_key, value in (cloud_tags or {}).items()
    }

    existing = {t.tag_key: t for t in await tag_repo.list_cloud_tags(resource.id)}

    for key, tag in existing.items():
        if key not in incoming:
            await session.delete(tag)
            await _record_change(
                session, resource.id, resource.model_id, "tag",
                field=key, old_value=tag.tag_value, new_value=None,
            )
        elif tag.tag_value != incoming[key][1]:
            old_value = tag.tag_value
            tag.tag_value = incoming[key][1]
            tag.raw_key = incoming[key][0]
            tag.synced_at = now
            await _record_change(
                session, resource.id, resource.model_id, "tag",
                field=key, old_value=old_value, new_value=tag.tag_value,
            )

    for key, (raw_key, value) in incoming.items():
        if key not in existing:
            await tag_repo.add_resource_tag(CmdbResourceTag(
                resource_id=resource.id,
                tag_key=key,
                tag_value=value,
                source="cloud",
                raw_key=raw_key,
                synced_at=now,
            ))
            await _record_change(
                session, resource.id, resource.model_id, "tag",
                field=key, old_value=None, new_value=value,
            )


async def _record_change(
    session: AsyncSession,
    resource_id: int,
    model_id: int,
    change_type: str,
    *,
    field: str | None = None,
    old_value: str | None = None,
    new_value: str | None = None,
) -> None:
    """记录变更日志（tag 类变更携带 field/old/new 明细）。"""
    log_repo = CmdbChangeLogRepo(session)
    log = CmdbChangeLog(
        resource_id=resource_id,
        model_id=model_id,
        change_type=change_type,
        source="discovery",
        field=field,
        old_value=old_value,
        new_value=new_value,
    )
    await log_repo.create(log)
