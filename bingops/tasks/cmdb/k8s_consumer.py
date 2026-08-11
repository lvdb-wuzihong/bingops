"""K8s 资源事件消费处理器（v2）。

消费 Kafka Topic: k8s-events-{cluster_id}，消息契约为 cmdb-informer 的 MQMessage。

处理逻辑：
1. resource_type → 模型映射（模型定义驱动，k8s_extractors）
2. 幂等校验（resource_version）+ 无实质变更直接跳过（防快照重放噪音）
3. Upsert / 软删除资源（cmdb_resources：model_id + fields JSONB）
4. 差异同步 K8s labels → cmdb_resource_tags（source='cloud'）
5. 按关系约束重建从属 + 关联边
6. 变更审计：仅 create/delete 记 change_log（附录 B #21 纪律）
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bingops.models.cmdb.change_log import CmdbChangeLog
from bingops.models.cmdb.model import CmdbModel, CmdbModelField
from bingops.models.cmdb.resource import CmdbResource
from bingops.models.cmdb.tag import CmdbResourceTag
from bingops.repositories.cmdb.change_log_repo import CmdbChangeLogRepo
from bingops.repositories.cmdb.resource_repo import CmdbResourceRepo
from bingops.repositories.cmdb.sync_task_repo import CmdbSyncTaskRepo
from bingops.repositories.cmdb.tag_repo import CmdbTagRepo
from bingops.schemas.cmdb.kafka_messages import K8sEventType, K8sResourceMessage
from bingops.tasks.cmdb import k8s_extractors
from bingops.tasks.cmdb.relationship_builder import (
    rebuild_k8s_relationships,
    remove_resource_edges,
)

logger = logging.getLogger(f"bingops.{__name__}")


def create_k8s_handler(session_factory: async_sessionmaker[AsyncSession]):
    """创建 K8s 事件处理函数（闭包注入 session_factory）。"""

    async def handle_k8s_event(message: K8sResourceMessage) -> None:
        """处理单条 K8s 资源变更消息。"""
        resource_type = message.resource_type
        model_code = k8s_extractors.RESOURCE_TYPE_TO_MODEL.get(resource_type)
        if model_code is None:
            if resource_type in k8s_extractors.UNMANAGED_RESOURCE_TYPES:
                logger.debug("Unmanaged K8s resource type, skipping", extra={"resource_type": resource_type})
            else:
                logger.warning("Unknown K8s resource type, skipping", extra={"resource_type": resource_type})
            return

        async with session_factory() as session:
            try:
                # 同步任务开关判断
                sync_repo = CmdbSyncTaskRepo(session)
                if not await sync_repo.is_enabled("k8s", message.cluster_id):
                    logger.debug("K8s sync disabled for cluster, skipping", extra={"cluster": message.cluster_id})
                    return

                # 模型注册表（code → id）+ 字段定义（落库前白名单过滤用）
                model_ids, field_codes = await _load_model_catalog(session)
                model_id = model_ids.get(model_code)
                if model_id is None:
                    logger.warning("Model not defined in registry, skipping", extra={"model_code": model_code})
                    return

                # 集群资源兜底创建（node/namespace 等从属边的父节点）
                await _ensure_cluster_resource(session, message, model_ids)

                if message.event_type == K8sEventType.DELETE:
                    await _handle_delete(session, model_id, message)
                else:
                    await _handle_upsert(session, model_id, model_code, model_ids, field_codes, message)

                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception(
                    "Failed to process K8s event",
                    extra={
                        "cluster": message.cluster_id,
                        "resource_type": message.resource_type,
                        "name": message.resource.name,
                        "event_type": message.event_type.value,
                    },
                )

    return handle_k8s_event


# ── Upsert / 删除 ──────────────────────────────────────────────────────────────


async def _handle_upsert(
    session: AsyncSession,
    model_id: int,
    model_code: str,
    model_ids: dict[str, int],
    field_codes: dict[int, set[str]],
    message: K8sResourceMessage,
) -> None:
    """Upsert K8s 资源（幂等 + 无变更跳过）。"""
    repo = CmdbResourceRepo(session)
    payload = message.resource
    cluster_id = message.cluster_id

    obj = payload.raw or {"spec": payload.spec or {}, "status": payload.status or {}}
    fields, status = k8s_extractors.extract(message.resource_type, obj)

    # 按库内模型字段定义过滤：模型变更后提取器产出的死键不落库
    fields, dropped = k8s_extractors.filter_by_model_fields(fields, field_codes.get(model_id, set()))
    if dropped:
        logger.warning(
            "Extractor produced fields outside model definition, dropped",
            extra={"model_code": model_code, "dropped_keys": dropped},
        )

    provider_id = (
        f"{cluster_id}/{payload.namespace}/{payload.name}"
        if payload.namespace else f"{cluster_id}/{payload.name}"
    )
    # include_deleted=True：命中软删记录时走复活路径（同名重建不撞唯一约束）
    existing = await repo.get_by_provider_id(
        model_id, "k8s", provider_id, cluster_id, include_deleted=True
    )

    # 幂等校验：incoming version <= current version → 跳过
    if existing and existing.resource_version and payload.resource_version:
        if _version_lte(payload.resource_version, existing.resource_version):
            logger.debug("K8s event skipped (version not newer)", extra={"provider_id": provider_id})
            return

    now = datetime.now(timezone.utc)
    if existing:
        # 无实质变更直接跳过（快照重放去噪：不写库、不记审计；软删记录除外，需复活）
        if (
            existing.deleted_at is None
            and existing.fields == fields
            and existing.status == status
            and existing.name == payload.name
        ):
            logger.debug("K8s event skipped (no effective change)", extra={"provider_id": provider_id})
            return

        existing.name = payload.name
        existing.status = status
        existing.fields = fields
        existing.resource_version = payload.resource_version or None
        existing.synced_at = now
        existing.source = "discovery"
        existing.deleted_at = None  # 恢复软删除
        await repo.update(existing)
        resource = existing
        logger.debug("K8s resource updated", extra={"provider_id": provider_id})
    else:
        resource = CmdbResource(
            model_id=model_id,
            provider="k8s",
            provider_id=provider_id,
            cloud_account=cluster_id,
            name=payload.name,
            status=status,
            fields=fields,
            resource_version=payload.resource_version or None,
            synced_at=now,
            source="discovery",
        )
        resource = await repo.create(resource)

        # 变更审计仅记 create（高频更新不记，防 pod 噪音）
        await _record_change(session, resource.id, model_id, "create")
        logger.info("K8s resource created", extra={"provider_id": provider_id, "model_code": model_code})

    # 差异同步 K8s labels → 资源标签
    await _sync_k8s_labels(session, resource, payload.labels)

    # 重建关系边
    await rebuild_k8s_relationships(session, resource, message, model_ids)


async def _handle_delete(
    session: AsyncSession,
    model_id: int,
    message: K8sResourceMessage,
) -> None:
    """软删除 K8s 资源并清理关系边。"""
    repo = CmdbResourceRepo(session)
    payload = message.resource
    cluster_id = message.cluster_id
    provider_id = (
        f"{cluster_id}/{payload.namespace}/{payload.name}"
        if payload.namespace else f"{cluster_id}/{payload.name}"
    )
    existing = await repo.get_by_provider_id(model_id, "k8s", provider_id, cluster_id)
    if existing is None:
        logger.debug("K8s resource not found for delete, skipping", extra={"provider_id": provider_id})
        return

    # 版本守卫：库内版本更新（如资源已同名重建）时丢弃迟到的旧 delete
    if (
        existing.resource_version
        and payload.resource_version
        and not _version_lte(existing.resource_version, payload.resource_version)
    ):
        logger.debug("K8s delete skipped (stale version)", extra={"provider_id": provider_id})
        return

    await repo.soft_delete(existing)
    await remove_resource_edges(session, existing.id)
    await _record_change(session, existing.id, model_id, "delete")
    logger.info("K8s resource soft-deleted", extra={"provider_id": provider_id})


# ── 集群资源兜底创建 ───────────────────────────────────────────────────────────


async def _ensure_cluster_resource(
    session: AsyncSession,
    message: K8sResourceMessage,
    model_ids: dict[str, int],
) -> None:
    """确保 k8s_cluster 资源存在（从属边的公共父节点）。"""
    cluster_model_id = model_ids.get("k8s_cluster")
    if not cluster_model_id:
        return

    repo = CmdbResourceRepo(session)
    cluster_id = message.cluster_id
    existing = await repo.get_by_provider_id(cluster_model_id, "k8s", cluster_id, cluster_id)
    if existing:
        return

    cluster = CmdbResource(
        model_id=cluster_model_id,
        provider="k8s",
        provider_id=cluster_id,
        cloud_account=cluster_id,
        name=cluster_id,
        status="running",
        fields={"cluster_type": message.cluster_type} if message.cluster_type else {},
        synced_at=datetime.now(timezone.utc),
        source="discovery",
    )
    await repo.create(cluster)
    await _record_change(session, cluster.id, cluster_model_id, "create")
    logger.info("K8s cluster resource auto-created", extra={"cluster": cluster_id})


# ── 标签差异同步 ───────────────────────────────────────────────────────────────


async def _sync_k8s_labels(
    session: AsyncSession, resource: CmdbResource, labels: dict[str, str],
) -> None:
    """差异同步 K8s labels → cmdb_resource_tags（source='cloud'）。

    新增 / 改值 / 清除失效标签，manual 标签不受影响。
    """
    tag_repo = CmdbTagRepo(session)
    now = datetime.now(timezone.utc)
    incoming = {f"k8s:{key}": value for key, value in labels.items()}

    existing = {tag.tag_key: tag for tag in await tag_repo.list_cloud_tags(resource.id)}
    for tag_key, tag in existing.items():
        if tag_key not in incoming:
            await session.delete(tag)
        elif tag.tag_value != incoming[tag_key]:
            tag.tag_value = incoming[tag_key]
            tag.synced_at = now

    for tag_key, value in incoming.items():
        if tag_key not in existing:
            session.add(CmdbResourceTag(
                resource_id=resource.id,
                tag_key=tag_key,
                tag_value=value,
                source="cloud",
                raw_key=tag_key.removeprefix("k8s:"),
                synced_at=now,
            ))
    await session.flush()


# ── 工具函数 ───────────────────────────────────────────────────────────────────


async def _load_model_catalog(session: AsyncSession) -> tuple[dict[str, int], dict[int, set[str]]]:
    """加载模型注册表（code → id）与各模型的字段定义 code 集合。"""
    models = await session.execute(select(CmdbModel))
    model_ids = {model.code: model.id for model in models.scalars().all()}

    field_rows = await session.execute(select(CmdbModelField.model_id, CmdbModelField.code))
    field_codes: dict[int, set[str]] = {}
    for model_id, code in field_rows.all():
        field_codes.setdefault(model_id, set()).add(code)
    return model_ids, field_codes


async def _record_change(
    session: AsyncSession,
    resource_id: int,
    model_id: int,
    change_type: str,
) -> None:
    """记录变更日志（仅 create/delete，来源 discovery）。"""
    log_repo = CmdbChangeLogRepo(session)
    log = CmdbChangeLog(
        resource_id=resource_id,
        model_id=model_id,
        change_type=change_type,
        source="discovery",
    )
    await log_repo.create(log)


def _version_lte(v1: str, v2: str) -> bool:
    """比较 K8s resourceVersion（纯数字字符串）。"""
    try:
        return int(v1) <= int(v2)
    except (ValueError, TypeError):
        return v1 <= v2
