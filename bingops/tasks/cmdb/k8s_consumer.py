"""K8s 资源事件消费处理器。

消费 Kafka Topic: k8s-events-{cluster_id}，将 K8s 资源变更同步到 CMDB。

处理逻辑：
1. 幂等校验（resource_version）
2. Upsert / 软删除资源
3. 同步云标签（K8s labels → cmdb_resource_tags）
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
from bingops.schemas.cmdb.kafka_messages import K8sEventType, K8sResourceMessage
from bingops.tasks.cmdb.relationship_builder import rebuild_k8s_relationships

logger = logging.getLogger(f"bingops.{__name__}")

# K8s Kind → CMDB resource_type 映射
KIND_TO_RESOURCE_TYPE: dict[str, str] = {
    "Pod": "k8s_pod",
    "Deployment": "k8s_deployment",
    "StatefulSet": "k8s_statefulset",
    "DaemonSet": "k8s_daemonset",
    "ReplicaSet": "k8s_replicaset",
    "Service": "k8s_service",
    "Ingress": "k8s_ingress",
    "ConfigMap": "k8s_configmap",
    "Secret": "k8s_secret",
    "PersistentVolumeClaim": "k8s_pvc",
    "Namespace": "k8s_namespace",
    "Node": "k8s_node",
}


def create_k8s_handler(session_factory: async_sessionmaker[AsyncSession]):
    """创建 K8s 事件处理函数（闭包注入 session_factory）。"""

    async def handle_k8s_event(message: K8sResourceMessage) -> None:
        """处理单条 K8s 资源变更消息。"""
        resource_type = KIND_TO_RESOURCE_TYPE.get(message.kind)
        if resource_type is None:
            logger.warning("Unknown K8s kind, skipping", extra={"kind": message.kind})
            return

        # provider_id = cluster/namespace/name（唯一标识）
        provider_id = f"{message.cluster}/{message.namespace}/{message.name}" if message.namespace else f"{message.cluster}/{message.name}"
        provider = "k8s"
        cloud_account = message.cluster

        async with session_factory() as session:
            try:
                # 同步任务开关判断
                sync_repo = CmdbSyncTaskRepo(session)
                if not await sync_repo.is_enabled("k8s", message.cluster):
                    logger.debug("K8s sync disabled for cluster, skipping", extra={"cluster": message.cluster})
                    return

                if message.event_type == K8sEventType.DELETE:
                    await _handle_delete(session, provider, resource_type, provider_id, cloud_account, message)
                else:
                    await _handle_upsert(session, provider, resource_type, provider_id, cloud_account, message)

                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception(
                    "Failed to process K8s event",
                    extra={
                        "cluster": message.cluster,
                        "kind": message.kind,
                        "name": message.name,
                        "event_type": message.event_type.value,
                    },
                )

    return handle_k8s_event


async def _handle_upsert(
    session: AsyncSession,
    provider: str,
    resource_type: str,
    provider_id: str,
    cloud_account: str,
    message: K8sResourceMessage,
) -> None:
    """Upsert K8s 资源（幂等）。"""
    repo = CmdbResourceRepo(session)
    existing = await repo.get_by_provider_id(provider, resource_type, provider_id, cloud_account)

    # 幂等校验：incoming version <= current version → 跳过
    if existing and existing.resource_version:
        if _version_lte(message.resource_version, existing.resource_version):
            logger.debug("K8s event skipped (version not newer)", extra={"provider_id": provider_id})
            return

    if existing:
        # 更新
        existing.name = message.name
        existing.status = _extract_status(message.data)
        existing.attributes = message.data
        existing.resource_version = message.resource_version
        existing.synced_at = datetime.now(timezone.utc)
        existing.source = "discovery"
        existing.deleted_at = None  # 恢复软删除
        await repo.update(existing)
        resource = existing

        # 记录变更
        await _record_change(session, resource.id, resource_type, "update", source="kafka")
        logger.info("K8s resource updated", extra={"provider_id": provider_id})
    else:
        # 新建
        resource = CmdbResource(
            provider=provider,
            resource_type=resource_type,
            provider_id=provider_id,
            cloud_account=cloud_account,
            name=message.name,
            region=None,
            zone=None,
            status=_extract_status(message.data),
            attributes=message.data,
            resource_version=message.resource_version,
            synced_at=datetime.now(timezone.utc),
            source="discovery",
        )
        resource = await repo.create(resource)

        await _record_change(session, resource.id, resource_type, "create", source="kafka")
        logger.info("K8s resource created", extra={"provider_id": provider_id})

    # 同步 K8s labels → 资源标签
    await _sync_k8s_labels(session, resource, message.labels)

    # 重建关系
    await rebuild_k8s_relationships(session, resource, message)


async def _handle_delete(
    session: AsyncSession,
    provider: str,
    resource_type: str,
    provider_id: str,
    cloud_account: str,
    message: K8sResourceMessage,
) -> None:
    """软删除 K8s 资源。"""
    repo = CmdbResourceRepo(session)
    existing = await repo.get_by_provider_id(provider, resource_type, provider_id, cloud_account)
    if existing is None:
        logger.debug("K8s resource not found for delete, skipping", extra={"provider_id": provider_id})
        return

    await repo.soft_delete(existing)
    await _record_change(session, existing.id, resource_type, "delete", source="kafka")
    logger.info("K8s resource soft-deleted", extra={"provider_id": provider_id})


async def _sync_k8s_labels(session: AsyncSession, resource: CmdbResource, labels: dict[str, str]) -> None:
    """将 K8s labels 同步为资源标签（source='cloud'）。"""
    from bingops.models.cmdb.tag import CmdbResourceTag
    from bingops.repositories.cmdb.tag_repo import CmdbTagRepo

    if not labels:
        return

    tag_repo = CmdbTagRepo(session)
    for key, value in labels.items():
        tag = CmdbResourceTag(
            resource_id=resource.id,
            tag_key=f"k8s:{key}",
            tag_value=value,
            source="cloud",
            raw_key=key,
            synced_at=datetime.now(timezone.utc),
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


def _extract_status(data: dict) -> str:
    """从 K8s 资源 data 中提取状态。"""
    status = data.get("status", {})
    phase = status.get("phase", "")
    if phase:
        return phase.lower()
    conditions = status.get("conditions", [])
    for cond in conditions:
        if cond.get("type") == "Ready":
            return "ready" if cond.get("status") == "True" else "not_ready"
    return "unknown"


def _version_lte(v1: str, v2: str) -> bool:
    """比较 K8s resourceVersion（纯数字字符串）。"""
    try:
        return int(v1) <= int(v2)
    except (ValueError, TypeError):
        return v1 <= v2
