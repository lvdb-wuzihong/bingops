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

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bingops.models.cmdb.change_log import CmdbChangeLog
from bingops.models.cmdb.model import CmdbModel, CmdbModelField
from bingops.models.cmdb.resource import CmdbResource
from bingops.models.cmdb.tag import CmdbResourceTag
from bingops.repositories.cmdb.change_log_repo import CmdbChangeLogRepo
from bingops.repositories.cmdb.relationship_repo import CmdbRelationshipRepo
from bingops.repositories.cmdb.resource_repo import CmdbResourceRepo
from bingops.repositories.cmdb.sync_task_repo import CmdbSyncTaskRepo
from bingops.repositories.cmdb.tag_repo import CmdbTagRepo
from bingops.schemas.cmdb.kafka_messages import (
    K8sEventType,
    K8sResourceMessage,
    K8sSyncType,
)
from bingops.tasks.cmdb import k8s_extractors
from bingops.tasks.cmdb.relationship_builder import (
    rebuild_k8s_relationships,
    remove_resource_edges,
)

logger = logging.getLogger(f"bingops.{__name__}")

# cluster_type → 托管厂商（附录 B #19：k8s_cluster.provider 标托管厂商，
# 是云与容器两个世界的桥；子资源继承集群 provider）。自建集群回退 k8s。
CLUSTER_TYPE_TO_PROVIDER: dict[str, str] = {"ack": "aliyun", "gke": "gcp"}

# K8s 标准拓扑标签（kubelet/cloud-provider 写入，值为厂商原生 region/zone，附录 B #28）
NODE_LABEL_REGION = "topology.kubernetes.io/region"
NODE_LABEL_ZONE = "topology.kubernetes.io/zone"

# ── 快照对账（附录 B #15）─────────────────────────────────────────────
# 快照消息（full_sync/periodic_sync）成批到达；空闲超过 GAP 视为一轮结束，
# 对 covered 模型做差集软删（补 watch 丢事件导致的漏删）。
SNAPSHOT_IDLE_GAP = 120.0
SWEEP_INTERVAL = 60.0

# message_id 去重（Kafka at-least-once 重放去噪）
DEDUP_CAP = 5000


@dataclass
class _SnapshotSession:
    """一轮快照的可见集（cluster 级）。"""

    seen: set[tuple[int, str]] = field(default_factory=set)
    covered_models: set[int] = field(default_factory=set)
    last_active: float = field(default_factory=time.monotonic)


_snapshot_sessions: dict[str, _SnapshotSession] = {}
_message_dedup: dict[str, OrderedDict[str, None]] = {}


def create_k8s_handler(session_factory: async_sessionmaker[AsyncSession]):
    """创建 K8s 事件处理函数（闭包注入 session_factory）。"""

    async def handle_k8s_event(message: K8sResourceMessage) -> None:
        """处理单条 K8s 资源变更消息。"""
        if _is_duplicate(message):
            logger.debug("Duplicate message_id, skipping", extra={"message_id": message.message_id})
            return
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
                # 同步任务门控：数据表驱动，默认拒绝。
                # 同一集群可拆分多个任务，任一启用任务命中即放行
                sync_repo = CmdbSyncTaskRepo(session)
                tasks = await sync_repo.list_by_type_and_target("k8s", message.cluster_id)
                active_tasks = [t for t in tasks if t.enabled]
                if not active_tasks:
                    logger.debug(
                        "K8s sync task not configured or disabled, skipping",
                        extra={"cluster": message.cluster_id},
                    )
                    return
                # resource_types 白名单过滤（空列表 = 同步全部类型）
                if not any(
                    not t.resource_types or resource_type in t.resource_types
                    for t in active_tasks
                ):
                    logger.debug(
                        "K8s resource type not in sync task, skipping",
                        extra={"cluster": message.cluster_id, "resource_type": resource_type},
                    )
                    return

                # 模型注册表（code → id）+ 字段定义（落库前白名单过滤用）
                model_ids, field_codes = await _load_model_catalog(session)
                model_id = model_ids.get(model_code)
                if model_id is None:
                    logger.warning("Model not defined in registry, skipping", extra={"model_code": model_code})
                    return

                # 集群资源兜底创建（node/namespace 等从属边的父节点），
                # 并解析本条消息应使用的 provider（托管厂商）与 region/zone（集群为唯一事实源）
                provider, region, zone = await _ensure_cluster_resource(session, message, model_ids)

                # 快照对账记账（#15）：seen 记录必须在 upsert 跳过路径之前，
                # 保证「无变更跳过」的资源也计入本轮可见集
                provider_id = (
                    f"{message.cluster_id}/{message.resource.namespace}/{message.resource.name}"
                    if message.resource.namespace
                    else f"{message.cluster_id}/{message.resource.name}"
                )
                is_snapshot = (
                    message.sync_type in (K8sSyncType.FULL_SYNC, K8sSyncType.PERIODIC_SYNC)
                    or message.event_type == K8sEventType.SNAPSHOT
                )
                await _snapshot_bookkeeping(session, message.cluster_id, is_snapshot, model_id, provider_id)

                if message.event_type == K8sEventType.DELETE:
                    await _handle_delete(session, model_id, provider, message)
                else:
                    await _handle_upsert(
                        session, model_id, model_code, model_ids, field_codes,
                        provider, region, zone, message,
                    )

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


# ── Upsert / 删除 ──────────────────────────────────────────────────────────


async def _heal_missing_belongs_to(
    session: AsyncSession,
    resource: CmdbResource,
    message: K8sResourceMessage,
    model_ids: dict[str, int],
) -> None:
    """跳过路径的边自愈：父资源后于本资源出现（如 cluster 兜底创建、namespace
    晚到）时首轮 belongs_to 建边失败，内容不变则永不修复。仅在无任何
    belongs_to 边时才补，避免高频无谓重建。
    """
    parents = await CmdbRelationshipRepo(session).get_parents(resource.id)
    if not parents:
        await rebuild_k8s_relationships(session, resource, message, model_ids)


async def _skip_side_effects(
    session: AsyncSession,
    resource: CmdbResource,
    message: K8sResourceMessage,
    model_ids: dict[str, int],
) -> None:
    """跳过路径的统一副作用：边自愈 + 标签差同步 + 应用关联重算。

    两个跳过分支（版本不新 / 无实质变更）都必须跑：应用主数据可能
    后于标签事件创建，靠每轮快照重算自愈，不要求操作先后顺序。
    """
    await _heal_missing_belongs_to(session, resource, message, model_ids)
    await _sync_k8s_labels(session, resource, message.resource.labels)
    from bingops.services.cmdb import business_app_service
    await business_app_service.refresh_app_links_from_tags(session, resource)


async def _handle_upsert(
    session: AsyncSession,
    model_id: int,
    model_code: str,
    model_ids: dict[str, int],
    field_codes: dict[int, set[str]],
    provider: str,
    region: str | None,
    zone: str | None,
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
        model_id, provider, provider_id, cluster_id, include_deleted=True
    )

    # 幂等校验：incoming version <= current version → 跳过
    if existing and existing.resource_version and payload.resource_version:
        if _version_lte(payload.resource_version, existing.resource_version):
            if existing.deleted_at is None:
                await _skip_side_effects(session, existing, message, model_ids)
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
            await _skip_side_effects(session, existing, message, model_ids)
            logger.debug("K8s event skipped (no effective change)", extra={"provider_id": provider_id})
            return

        existing.name = payload.name
        existing.status = status
        existing.fields = fields
        if region is not None:
            existing.region = region
            existing.zone = zone
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
            provider=provider,
            provider_id=provider_id,
            cloud_account=cluster_id,
            name=payload.name,
            region=region,
            zone=zone,
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

    # 应用关联物化（#13）：按 k8s:app 标签重算 tag 派生关联
    from bingops.services.cmdb import business_app_service
    await business_app_service.refresh_app_links_from_tags(session, resource)

    # 重建关系边
    await rebuild_k8s_relationships(session, resource, message, model_ids)


async def _handle_delete(
    session: AsyncSession,
    model_id: int,
    provider: str,
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
    existing = await repo.get_by_provider_id(model_id, provider, provider_id, cluster_id)
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
) -> tuple[str, str | None, str | None]:
    """确保 k8s_cluster 资源存在，返回本条消息应使用的 (provider, region, zone)。

    provider 语义（附录 B #19）：托管厂商（ack→aliyun / gke→gcp，自建→k8s）。
    集群实例是厂商的唯一事实源：已存在则沿用其 provider（保证子资源与集群一致），
    不存在则按 cluster_type 映射创建。

    region/zone 语义（附录 B #28）：集群实例同为地域唯一事实源，由 node 消息的
    标准拓扑标签首次识别写入（仅首次，防多 zone 集群 zone 值抖动）；
    子资源 upsert 时继承集群 region/zone。
    """
    provider = CLUSTER_TYPE_TO_PROVIDER.get(message.cluster_type, "k8s")
    node_region, node_zone = _node_topology(message)
    cluster_model_id = model_ids.get("k8s_cluster")
    if not cluster_model_id:
        return provider, node_region, node_zone

    repo = CmdbResourceRepo(session)
    cluster_id = message.cluster_id
    existing = await repo.find_by_provider_id(cluster_model_id, cluster_id, cluster_id)
    if existing:
        if existing.deleted_at is not None:
            existing.deleted_at = None
            await repo.update(existing)
        # node 消息首次带来 region：写集群实例并批量回填存量空白地域
        if node_region and existing.region is None:
            existing.region = node_region
            existing.zone = node_zone
            await repo.update(existing)
            await _backfill_cluster_region(session, cluster_id, node_region, node_zone)
        return existing.provider, existing.region, existing.zone

    cluster = CmdbResource(
        model_id=cluster_model_id,
        provider=provider,
        provider_id=cluster_id,
        cloud_account=cluster_id,
        name=cluster_id,
        status="running",
        region=node_region,
        zone=node_zone,
        fields={"cluster_type": message.cluster_type} if message.cluster_type else {},
        synced_at=datetime.now(timezone.utc),
        source="discovery",
    )
    await repo.create(cluster)
    await _record_change(session, cluster.id, cluster_model_id, "create")
    logger.info("K8s cluster resource auto-created", extra={"cluster": cluster_id})
    return provider, node_region, node_zone


def _node_topology(message: K8sResourceMessage) -> tuple[str | None, str | None]:
    """提取 node 消息标准拓扑标签中的 region/zone；非 node 消息返回 (None, None)。

    标签缺失（如自建集群无 cloud-provider）时返回 None，地域留空，优雅降级。
    """
    if message.resource_type != "nodes":
        return None, None
    labels = message.resource.labels or {}
    return labels.get(NODE_LABEL_REGION), labels.get(NODE_LABEL_ZONE)


async def _backfill_cluster_region(
    session: AsyncSession, cluster_id: str, region: str, zone: str | None,
) -> None:
    """集群 region 首次识别时，批量回填该集群 discovery 资源的历史空白地域。

    无变更跳过分支只比对 fields/status/name，空白 region 不会随自然更新自愈，
    需一次性批量回填；manual 资源与软删资源不受影响。
    """
    result = await session.execute(
        update(CmdbResource)
        .where(
            CmdbResource.cloud_account == cluster_id,
            CmdbResource.source == "discovery",
            CmdbResource.deleted_at.is_(None),
            CmdbResource.region.is_(None),
        )
        .values(region=region, zone=zone)
    )
    logger.info(
        "Cluster region backfilled",
        extra={"cluster": cluster_id, "rows": result.rowcount},
    )


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


# ── 工具函数 ─────────────────────────────────────────────────────────────


def _is_duplicate(message: K8sResourceMessage) -> bool:
    """message_id 去重（有界 LRU，防 Kafka 重放重复建/审）。"""
    bucket = _message_dedup.setdefault(message.cluster_id, OrderedDict())
    if message.message_id in bucket:
        return True
    bucket[message.message_id] = None
    while len(bucket) > DEDUP_CAP:
        bucket.popitem(last=False)
    return False


async def _snapshot_bookkeeping(
    session: AsyncSession,
    cluster_id: str,
    is_snapshot: bool,
    model_id: int,
    provider_id: str,
) -> None:
    """快照会话记账：快照消息累加可见集；watch 消息触发空闲会话终结。"""
    now = time.monotonic()
    sess = _snapshot_sessions.get(cluster_id)
    if is_snapshot:
        if sess is None or now - sess.last_active > SNAPSHOT_IDLE_GAP:
            if sess is not None:
                # 上一轮已空闲超时（两轮 periodic 之间的间隙），先终结再开新轮
                await _finalize_snapshot_session(session, cluster_id, sess)
            sess = _SnapshotSession()
            _snapshot_sessions[cluster_id] = sess
        sess.last_active = now
        sess.covered_models.add(model_id)
        sess.seen.add((model_id, provider_id))
    elif sess is not None and now - sess.last_active > SNAPSHOT_IDLE_GAP:
        # 快照轮已结束且后续来了 watch 事件：懒终结
        _snapshot_sessions.pop(cluster_id, None)
        await _finalize_snapshot_session(session, cluster_id, sess)


async def _finalize_snapshot_session(
    session: AsyncSession, cluster_id: str, sess: _SnapshotSession,
) -> None:
    """一轮快照结束：covered 模型中本轮未出现的 discovery 资源差集软删。"""
    repo = CmdbResourceRepo(session)
    deleted = 0
    for model_id in sess.covered_models:
        rows = await session.execute(
            select(CmdbResource).where(
                CmdbResource.model_id == model_id,
                CmdbResource.cloud_account == cluster_id,
                CmdbResource.source == "discovery",
                CmdbResource.deleted_at.is_(None),
            )
        )
        for res in rows.scalars().all():
            if (res.model_id, res.provider_id) in sess.seen:
                continue
            await repo.soft_delete(res)
            await remove_resource_edges(session, res.id)
            await _record_change(session, res.id, model_id, "delete")
            deleted += 1
    logger.info(
        "Snapshot reconciliation completed",
        extra={"cluster": cluster_id, "deleted": deleted,
               "covered_models": len(sess.covered_models)},
    )


async def snapshot_sweep_loop(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """周期扫尾：空闲超时的快照会话即使无新消息也终结对账。"""
    while True:
        await asyncio.sleep(SWEEP_INTERVAL)
        now = time.monotonic()
        idle = [
            (cid, sess)
            for cid, sess in list(_snapshot_sessions.items())
            if now - sess.last_active > SNAPSHOT_IDLE_GAP
        ]
        for cid, sess in idle:
            _snapshot_sessions.pop(cid, None)
            async with session_factory() as session:
                try:
                    await _finalize_snapshot_session(session, cid, sess)
                    await session.commit()
                except Exception:
                    await session.rollback()
                    logger.exception("Snapshot reconciliation failed", extra={"cluster": cid})


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
