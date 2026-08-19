"""CMDB K8s 关系重建引擎（v2）。

在资源 Upsert / 软删除后，按 cmdb_model_relations 中录入的关系约束重建实例边：

从属（belongs_to）：
- #17 namespace → cluster（集群归属）
- #18 pv → cluster（集群归属，PV 是集群级资源）
- #19 workload → namespace（命名空间归属）
- #20 service → namespace（命名空间归属）
- #21 pvc → namespace（命名空间归属）
- #22 pod → workload（属主负载）
- #23 pod → node（调度于）
- #24 pod → namespace（命名空间归属，裸 Pod 兜底）
- #52 node → cluster（集群归属）

关联（relates_to）：
- #39 service → pod（selector 匹配）
- #43 pod → pvc（使用存储）
- #53 pvc → pv（绑定）
- #35/#36 node → aliyun_ecs/gcp_compute（承载于，跨云桥接：instance_id 精确匹配优先、internal_ip 兜底；
  云主机入库时反向孤儿认领，见 adopt_node_host_edges）

跨云桥接边（#15/#16/#37/#38/#40/#41/#46/#47）依赖云侧资源，由云链路 v2 重建。

策略：从属边对子节点整包替换（先删后建）；关联边按语义槽位替换。
边写入不记 change_log（附录 B #21 纪律，防高频噪音）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.models.cmdb.relationship import CmdbBelongsTo, CmdbRelatesTo
from bingops.models.cmdb.resource import CmdbResource
from bingops.models.cmdb.tag import CmdbResourceTag
from bingops.repositories.cmdb.model_repo import CmdbModelRepo
from bingops.repositories.cmdb.relationship_repo import CmdbRelationshipRepo
from bingops.repositories.cmdb.resource_repo import CmdbResourceRepo
from bingops.repositories.cmdb.tag_repo import CmdbTagRepo
from bingops.schemas.cmdb.kafka_messages import K8sResourceData, K8sResourceMessage

logger = logging.getLogger(f"bingops.{__name__}")

# 边语义描述，与 cmdb_model_relations.relation_name 保持一致
DESC_CLUSTER_BELONG = "集群归属"
DESC_NS_BELONG = "命名空间归属"
DESC_OWNER_WORKLOAD = "属主负载"
DESC_SCHEDULED_ON = "调度于"
DESC_SELECTOR_MATCH = "selector 匹配"
DESC_USES_STORAGE = "使用存储"
DESC_PVC_BOUND = "绑定"
DESC_HOSTED_ON = "承载于"

# Pod 属主中可直接映射为 k8s_workload 的 Kind
_WORKLOAD_KINDS = {"Deployment", "StatefulSet", "DaemonSet"}


async def rebuild_k8s_relationships(
    session: AsyncSession,
    resource: CmdbResource,
    message: K8sResourceMessage,
    model_ids: dict[str, int],
) -> None:
    """重建单个 K8s 资源的关系边（Upsert 后调用）。"""
    model_code_by_id = {model_id: code for code, model_id in model_ids.items()}
    model_code = model_code_by_id.get(resource.model_id)
    if model_code is None:
        return

    rel_repo = CmdbRelationshipRepo(session)
    res_repo = CmdbResourceRepo(session)
    cluster_id = message.cluster_id
    namespace = message.resource.namespace
    payload = message.resource

    # 先清空该资源的从属边，再按约束重建（整包替换）
    await rel_repo.delete_belongs_to_by_child(resource.id)

    if model_code == "k8s_namespace":
        cluster = await _find_cluster(res_repo, model_ids, cluster_id)
        if cluster:
            await _add_belongs_to(rel_repo, resource.id, cluster.id, DESC_CLUSTER_BELONG)

    elif model_code == "k8s_node":
        cluster = await _find_cluster(res_repo, model_ids, cluster_id)
        if cluster:
            await _add_belongs_to(rel_repo, resource.id, cluster.id, DESC_CLUSTER_BELONG)
        # 跨云桥接：节点 → 云主机（#35/#36）
        await rebuild_node_host_edge(session, resource)

    elif model_code == "k8s_pv":
        cluster = await _find_cluster(res_repo, model_ids, cluster_id)
        if cluster:
            await _add_belongs_to(rel_repo, resource.id, cluster.id, DESC_CLUSTER_BELONG)

    elif model_code in ("k8s_workload", "k8s_service", "k8s_pvc"):
        ns = await _find_namespace(res_repo, model_ids, cluster_id, namespace)
        if ns:
            await _add_belongs_to(rel_repo, resource.id, ns.id, DESC_NS_BELONG)

    elif model_code == "k8s_pod":
        await _rebuild_pod_edges(session, rel_repo, res_repo, resource, payload, cluster_id, namespace, model_ids)

    # 关联边按语义槽位替换重建
    if model_code == "k8s_service":
        await _rebuild_service_pod_edges(session, rel_repo, resource, cluster_id, namespace, model_ids)
    elif model_code == "k8s_pod":
        await _rebuild_pod_pvc_edges(session, rel_repo, res_repo, resource, cluster_id, namespace, model_ids)
        await _rebuild_pod_inbound_service_edges(session, rel_repo, resource, payload, cluster_id, namespace, model_ids)
    elif model_code == "k8s_pvc":
        await _rebuild_pvc_pv_edges(session, rel_repo, res_repo, resource, cluster_id, model_ids)


async def remove_resource_edges(session: AsyncSession, resource_id: int) -> None:
    """软删除资源时清理其全部关系边。"""
    rel_repo = CmdbRelationshipRepo(session)
    removed = await rel_repo.delete_relations_of(resource_id)
    if removed:
        logger.debug("Resource edges removed on delete", extra={"resource_id": resource_id, "count": removed})


# ── K8s 节点 ↔ 云主机桥接边（#35/#36 承载于）──────────────────────


async def _resolve_host_for_node(
    res_repo: CmdbResourceRepo, model_repo: CmdbModelRepo, node: CmdbResource,
) -> CmdbResource | None:
    """按 instance_id 精确匹配优先、internal_ip 兜底解析节点承载的云主机。"""
    host_code = {"aliyun": "aliyun_ecs", "gcp": "gcp_compute"}.get(node.provider)
    if not host_code:
        return None  # 自建集群无云主机对端
    model = await model_repo.get_model_by_code(host_code)
    if model is None:
        return None
    fields = node.fields or {}
    instance_id = fields.get("instance_id")
    if instance_id:
        if node.provider == "aliyun":
            # ACK providerID 解析出 i-xxx，即 ECS provider_id
            host = await res_repo.find_by_provider_id_any_account(
                model.id, node.provider, instance_id,
            )
        else:
            # GKE providerID gce://project/zone/name 解析出实例名
            host = await res_repo.find_by_name_any_account(
                model.id, node.provider, instance_id,
            )
        if host is not None:
            return host
    internal_ip = fields.get("internal_ip")
    if internal_ip:
        hosts = await res_repo.list_by_field_value(
            model.id, node.provider, "private_ip", internal_ip,
        )
        if hosts:
            return hosts[0]
    return None


async def rebuild_node_host_edge(session: AsyncSession, node: CmdbResource) -> None:
    """k8s_node → aliyun_ecs/gcp_compute relates_to 承载于（槽位整包替换，diff 跳过）。"""
    rel_repo = CmdbRelationshipRepo(session)
    res_repo = CmdbResourceRepo(session)
    model_repo = CmdbModelRepo(session)

    host = await _resolve_host_for_node(res_repo, model_repo, node)
    expected = {host.id} if host is not None else set()

    current = [
        r for r in await rel_repo.get_relations_from(node.id)
        if r.description == DESC_HOSTED_ON
    ]
    if {r.target_id for r in current} == expected:
        return
    for r in current:
        await rel_repo.delete_relates_to(r.id)
    if host is not None:
        await rel_repo.create_relates_to(CmdbRelatesTo(
            source_id=node.id,
            target_id=host.id,
            description=DESC_HOSTED_ON,
            synced_at=datetime.now(timezone.utc),
            source="discovery",
        ))


async def adopt_node_host_edges(
    session: AsyncSession, host: CmdbResource, instance_key: str, internal_ip: str | None,
) -> None:
    """云主机入库后反向孤儿认领：为已存在但还没建边的节点补承载于边。"""
    model_repo = CmdbModelRepo(session)
    res_repo = CmdbResourceRepo(session)
    node_model = await model_repo.get_model_by_code("k8s_node")
    if node_model is None:
        return
    nodes: list[CmdbResource] = []
    if instance_key:
        nodes.extend(await res_repo.list_by_field_value(
            node_model.id, host.provider, "instance_id", instance_key,
        ))
    if internal_ip:
        for n in await res_repo.list_by_field_value(
            node_model.id, host.provider, "internal_ip", internal_ip,
        ):
            if n.id not in {x.id for x in nodes}:
                nodes.append(n)
    for node in nodes:
        await rebuild_node_host_edge(session, node)


# ── Pod 从属边 ─────────────────────────────────────────────────────────────────


async def _rebuild_pod_edges(
    session: AsyncSession,
    rel_repo: CmdbRelationshipRepo,
    res_repo: CmdbResourceRepo,
    pod: CmdbResource,
    payload: K8sResourceData,
    cluster_id: str,
    namespace: str,
    model_ids: dict[str, int],
) -> None:
    """Pod: #22 属主负载 + #23 调度于，无属主时 #24 命名空间兜底。"""
    obj = payload.raw or {}
    owner_refs = (obj.get("metadata") or {}).get("ownerReferences") or []

    has_workload_edge = False
    if owner_refs:
        owner = owner_refs[0]
        workload = await _resolve_owner_workload(res_repo, owner, cluster_id, namespace, model_ids)
        if workload:
            await _add_belongs_to(rel_repo, pod.id, workload.id, DESC_OWNER_WORKLOAD)
            has_workload_edge = True

    node_name = ((obj.get("spec") or {}).get("nodeName"))
    if node_name:
        node = await res_repo.find_by_name(model_ids.get("k8s_node", 0), cluster_id, node_name)
        if node:
            await _add_belongs_to(rel_repo, pod.id, node.id, DESC_SCHEDULED_ON)

    if not has_workload_edge and namespace:
        ns = await _find_namespace(res_repo, model_ids, cluster_id, namespace)
        if ns:
            await _add_belongs_to(rel_repo, pod.id, ns.id, DESC_NS_BELONG)


async def _resolve_owner_workload(
    res_repo: CmdbResourceRepo,
    owner: dict,
    cluster_id: str,
    namespace: str,
    model_ids: dict[str, int],
) -> CmdbResource | None:
    """把 Pod 的 ownerReference 解析为 k8s_workload 资源。

    Deployment/StatefulSet/DaemonSet 直接按名定位；
    ReplicaSet 无独立模型，剥离尾部 -<hash> 段回溯到 Deployment。
    """
    workload_model_id = model_ids.get("k8s_workload")
    if not workload_model_id:
        return None

    kind = owner.get("kind")
    name = owner.get("name") or ""
    if kind in _WORKLOAD_KINDS:
        return await res_repo.find_by_name(workload_model_id, cluster_id, name, namespace)
    if kind == "ReplicaSet" and "-" in name:
        deployment_name = name.rsplit("-", 1)[0]
        return await res_repo.find_by_name(workload_model_id, cluster_id, deployment_name, namespace)
    return None


# ── Service ↔ Pod 关联边 ───────────────────────────────────────────────────────


async def _rebuild_service_pod_edges(
    session: AsyncSession,
    rel_repo: CmdbRelationshipRepo,
    service: CmdbResource,
    cluster_id: str,
    namespace: str,
    model_ids: dict[str, int],
) -> None:
    """Service 变更时：按 selector 重建 Service → Pod 边。"""
    selector = service.fields.get("selector") or {}
    await rel_repo.delete_relates_to_by_source(service.id)
    if not selector or not namespace:
        return

    pod_labels = await _load_namespace_pod_labels(session, cluster_id, namespace, model_ids)
    now = datetime.now(timezone.utc)
    for pod_id, labels in pod_labels.items():
        if labels and all(labels.get(k) == v for k, v in selector.items()):
            await rel_repo.create_relates_to(CmdbRelatesTo(
                source_id=service.id,
                target_id=pod_id,
                description=DESC_SELECTOR_MATCH,
                synced_at=now,
                source="discovery",
            ))


async def _rebuild_pod_inbound_service_edges(
    session: AsyncSession,
    rel_repo: CmdbRelationshipRepo,
    pod: CmdbResource,
    payload: K8sResourceData,
    cluster_id: str,
    namespace: str,
    model_ids: dict[str, int],
) -> None:
    """Pod 变更时：刷新指向该 Pod 的 Service 边（入边重建）。"""
    if not namespace:
        return

    # 清理指向该 Pod 的 selector 匹配边
    for rel in await rel_repo.get_relations_to(pod.id, DESC_SELECTOR_MATCH):
        await rel_repo.delete_relates_to(rel.id)

    labels = payload.labels or {}
    if not labels:
        return

    service_model_id = model_ids.get("k8s_service")
    if not service_model_id:
        return
    services = await _list_model_resources(session, service_model_id, cluster_id, namespace)
    now = datetime.now(timezone.utc)
    for service in services:
        selector = service.fields.get("selector") or {}
        if selector and all(labels.get(k) == v for k, v in selector.items()):
            await rel_repo.create_relates_to(CmdbRelatesTo(
                source_id=service.id,
                target_id=pod.id,
                description=DESC_SELECTOR_MATCH,
                synced_at=now,
                source="discovery",
            ))


async def _load_namespace_pod_labels(
    session: AsyncSession,
    cluster_id: str,
    namespace: str,
    model_ids: dict[str, int],
) -> dict[int, dict[str, str]]:
    """加载某 namespace 下所有 Pod 的 labels（来自云标签通道，tag_key 带 k8s: 前缀）。"""
    pod_model_id = model_ids.get("k8s_pod")
    if not pod_model_id:
        return {}
    pods = await _list_model_resources(session, pod_model_id, cluster_id, namespace)
    if not pods:
        return {}

    tag_repo = CmdbTagRepo(session)
    tags = await tag_repo.list_cloud_tags_by_resources([pod.id for pod in pods])
    labels_by_pod: dict[int, dict[str, str]] = {pod.id: {} for pod in pods}
    for tag in tags:
        if tag.tag_key.startswith("k8s:") and tag.raw_key:
            labels_by_pod.setdefault(tag.resource_id, {})[tag.raw_key] = tag.tag_value
    return labels_by_pod


# ── Pod → PVC / PVC → PV 关联边 ────────────────────────────────────────────────


async def _rebuild_pod_pvc_edges(
    session: AsyncSession,
    rel_repo: CmdbRelationshipRepo,
    res_repo: CmdbResourceRepo,
    pod: CmdbResource,
    cluster_id: str,
    namespace: str,
    model_ids: dict[str, int],
) -> None:
    """Pod: 从 spec.volumes 提取 persistentVolumeClaim 引用（#43 使用存储）。

    PVC 名称清单由提取器写入 fields['_pvc_names']（下划线前缀 = 内部元数据，
    前端列表不渲染），此处只负责按名定位并建边。
    """
    await rel_repo.delete_relates_to_by_source(pod.id)
    pvc_names = pod.fields.get("_pvc_names") or []
    now = datetime.now(timezone.utc)
    pvc_model_id = model_ids.get("k8s_pvc")
    for pvc_name in pvc_names:
        if not pvc_model_id:
            break
        pvc = await res_repo.find_by_name(pvc_model_id, cluster_id, pvc_name, namespace)
        if pvc:
            await rel_repo.create_relates_to(CmdbRelatesTo(
                source_id=pod.id,
                target_id=pvc.id,
                description=DESC_USES_STORAGE,
                synced_at=now,
                source="discovery",
            ))


async def _rebuild_pvc_pv_edges(
    session: AsyncSession,
    rel_repo: CmdbRelationshipRepo,
    res_repo: CmdbResourceRepo,
    pvc: CmdbResource,
    cluster_id: str,
    model_ids: dict[str, int],
) -> None:
    """PVC: volume_name 匹配 PV（#53 绑定）。"""
    await rel_repo.delete_relates_to_by_source(pvc.id)
    volume_name = pvc.fields.get("volume_name")
    pv_model_id = model_ids.get("k8s_pv")
    if not volume_name or not pv_model_id:
        return
    pv = await res_repo.find_by_name(pv_model_id, cluster_id, volume_name)
    if pv:
        await rel_repo.create_relates_to(CmdbRelatesTo(
            source_id=pvc.id,
            target_id=pv.id,
            description=DESC_PVC_BOUND,
            synced_at=datetime.now(timezone.utc),
            source="discovery",
        ))


# ── 工具函数 ───────────────────────────────────────────────────────────────────


async def _find_cluster(
    res_repo: CmdbResourceRepo, model_ids: dict[str, int], cluster_id: str,
) -> CmdbResource | None:
    cluster_model_id = model_ids.get("k8s_cluster")
    if not cluster_model_id:
        return None
    return await res_repo.get_by_provider_id(cluster_model_id, "k8s", cluster_id, cluster_id)


async def _find_namespace(
    res_repo: CmdbResourceRepo, model_ids: dict[str, int], cluster_id: str, namespace: str,
) -> CmdbResource | None:
    if not namespace:
        return None
    ns_model_id = model_ids.get("k8s_namespace")
    if not ns_model_id:
        return None
    return await res_repo.get_by_provider_id(
        ns_model_id, "k8s", f"{cluster_id}/{namespace}", cluster_id,
    )


async def _list_model_resources(
    session: AsyncSession, model_id: int, cluster_id: str, namespace: str,
) -> list[CmdbResource]:
    """列出某模型在指定集群 + namespace 下的资源（fields.namespace 匹配）。"""
    result = await session.execute(
        select(CmdbResource).where(
            CmdbResource.model_id == model_id,
            CmdbResource.cloud_account == cluster_id,
            CmdbResource.fields["namespace"].astext == namespace,
            CmdbResource.deleted_at.is_(None),
        )
    )
    return list(result.scalars().all())


async def _add_belongs_to(
    rel_repo: CmdbRelationshipRepo, child_id: int, parent_id: int, description: str,
) -> None:
    """创建一条从属边。"""
    await rel_repo.create_belongs_to(CmdbBelongsTo(
        child_id=child_id,
        parent_id=parent_id,
        description=description,
        synced_at=datetime.now(timezone.utc),
        source="discovery",
    ))
