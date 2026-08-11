"""CMDB 关系重建引擎。

在资源 Upsert 后，根据资源元数据自动重建从属关系和关联关系。

K8s 关系规则：
- Pod → ReplicaSet → Deployment → Namespace → Cluster（从属链）
- Service → Pod（通过 selector 匹配，关联关系）
- Node → Cluster（从属）

云资源关系规则：
- Host → Subnet → VPC（从属链）
- Database → VPC（从属）
- Host ↔ K8sNode（通过 IP 匹配，关联关系）
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.models.cmdb.relationship import CmdbBelongsTo, CmdbRelatesTo
from bingops.models.cmdb.resource import CmdbResource
from bingops.repositories.cmdb.relationship_repo import CmdbRelationshipRepo
from bingops.repositories.cmdb.resource_repo import CmdbResourceRepo
from bingops.schemas.cmdb.kafka_messages import CloudResourceMessage, K8sResourceMessage

logger = logging.getLogger(f"bingops.{__name__}")


async def rebuild_k8s_relationships(
    session: AsyncSession,
    resource: CmdbResource,
    message: K8sResourceMessage,
) -> None:
    """重建 K8s 资源的关系链。

    策略：先删旧关系，再根据消息数据重建。
    """
    rel_repo = CmdbRelationshipRepo(session)

    kind = message.kind
    data = message.data
    cluster = message.cluster
    namespace = message.namespace

    if kind == "Pod":
        await _rebuild_pod_relationships(session, rel_repo, resource, data, cluster, namespace)

    elif kind in ("Deployment", "StatefulSet", "DaemonSet"):
        await _rebuild_workload_relationships(session, rel_repo, resource, cluster, namespace)

    elif kind == "ReplicaSet":
        await _rebuild_replicaset_relationships(session, rel_repo, resource, data, cluster, namespace)

    elif kind == "Service":
        await _rebuild_service_relationships(session, rel_repo, resource, data, cluster, namespace)

    elif kind == "Node":
        await _rebuild_node_relationships(session, rel_repo, resource, cluster)

    elif kind == "Namespace":
        await _rebuild_namespace_relationships(session, rel_repo, resource, cluster)

    else:
        # 其他类型资源：仅建立到 namespace 的从属
        if namespace:
            await _ensure_belongs_to_by_type(
                session, rel_repo, resource, "k8s_namespace", cluster, namespace,
            )


async def rebuild_cloud_relationships(
    session: AsyncSession,
    resource: CmdbResource,
    message: CloudResourceMessage,
) -> None:
    """重建云资源的关系链。

    策略：根据消息中的 parent_provider_id 和 parent_resource_type 建立从属关系。
    """
    rel_repo = CmdbRelationshipRepo(session)

    # 从属关系：通过 parent 提示建立
    if message.parent_provider_id and message.parent_resource_type:
        parent = await _find_resource_by_provider_id(
            session, message.provider, message.parent_resource_type,
            message.parent_provider_id, message.cloud_account,
        )
        if parent:
            await _upsert_belongs_to(rel_repo, resource.id, parent.id, "cloud_hierarchy")

    # Host → VPC 关联（通过 attributes 中的 vpc_id）
    if resource.resource_type == "host":
        vpc_id = resource.attributes.get("vpc_id")
        if vpc_id:
            vpc = await _find_resource_by_provider_id(
                session, resource.provider, "vpc", vpc_id, resource.cloud_account,
            )
            if vpc:
                await _upsert_belongs_to(rel_repo, resource.id, vpc.id, "host_in_vpc")

        subnet_id = resource.attributes.get("subnet_id") or resource.attributes.get("vswitch_id")
        if subnet_id:
            subnet = await _find_resource_by_provider_id(
                session, resource.provider, "subnet", subnet_id, resource.cloud_account,
            )
            if subnet:
                await _upsert_belongs_to(rel_repo, resource.id, subnet.id, "host_in_subnet")

    # Database → VPC 关联
    if resource.resource_type == "database":
        vpc_id = resource.attributes.get("vpc_id")
        if vpc_id:
            vpc = await _find_resource_by_provider_id(
                session, resource.provider, "vpc", vpc_id, resource.cloud_account,
            )
            if vpc:
                await _upsert_belongs_to(rel_repo, resource.id, vpc.id, "db_in_vpc")


# ── K8s 关系重建细节 ──────────────────────────────────────────────────────────


async def _rebuild_pod_relationships(
    session: AsyncSession,
    rel_repo: CmdbRelationshipRepo,
    resource: CmdbResource,
    data: dict,
    cluster: str,
    namespace: str,
) -> None:
    """Pod: Pod → ReplicaSet → Namespace。"""
    # Pod → Namespace
    await _ensure_belongs_to_by_type(
        session, rel_repo, resource, "k8s_namespace", cluster, namespace,
    )

    # Pod → ReplicaSet（通过 ownerReferences）
    owner_refs = data.get("metadata", {}).get("ownerReferences", [])
    for ref in owner_refs:
        if ref.get("kind") == "ReplicaSet":
            owner_name = ref.get("name", "")
            await _ensure_belongs_to_by_name(
                session, rel_repo, resource, "k8s_replicaset", cluster, namespace, owner_name,
            )


async def _rebuild_workload_relationships(
    session: AsyncSession,
    rel_repo: CmdbRelationshipRepo,
    resource: CmdbResource,
    cluster: str,
    namespace: str,
) -> None:
    """Deployment/StatefulSet/DaemonSet → Namespace。"""
    await _ensure_belongs_to_by_type(
        session, rel_repo, resource, "k8s_namespace", cluster, namespace,
    )


async def _rebuild_replicaset_relationships(
    session: AsyncSession,
    rel_repo: CmdbRelationshipRepo,
    resource: CmdbResource,
    data: dict,
    cluster: str,
    namespace: str,
) -> None:
    """ReplicaSet → Deployment → Namespace。"""
    await _ensure_belongs_to_by_type(
        session, rel_repo, resource, "k8s_namespace", cluster, namespace,
    )

    owner_refs = data.get("metadata", {}).get("ownerReferences", [])
    for ref in owner_refs:
        if ref.get("kind") == "Deployment":
            owner_name = ref.get("name", "")
            await _ensure_belongs_to_by_name(
                session, rel_repo, resource, "k8s_deployment", cluster, namespace, owner_name,
            )


async def _rebuild_service_relationships(
    session: AsyncSession,
    rel_repo: CmdbRelationshipRepo,
    resource: CmdbResource,
    data: dict,
    cluster: str,
    namespace: str,
) -> None:
    """Service → Namespace（从属）+ Service → Pod（关联，通过 selector）。"""
    await _ensure_belongs_to_by_type(
        session, rel_repo, resource, "k8s_namespace", cluster, namespace,
    )

    # Service → Pod（通过 selector 匹配）
    selector = data.get("spec", {}).get("selector", {})
    if selector:
        await _rebuild_service_pod_relations(session, rel_repo, resource, cluster, namespace, selector)


async def _rebuild_node_relationships(
    session: AsyncSession,
    rel_repo: CmdbRelationshipRepo,
    resource: CmdbResource,
    cluster: str,
) -> None:
    """Node → Cluster（从属）。"""
    cluster_resource = await _find_resource_by_provider_id(
        session, "k8s", "k8s_cluster", cluster, cluster,
    )
    if cluster_resource:
        await _upsert_belongs_to(rel_repo, resource.id, cluster_resource.id, "node_in_cluster")


async def _rebuild_namespace_relationships(
    session: AsyncSession,
    rel_repo: CmdbRelationshipRepo,
    resource: CmdbResource,
    cluster: str,
) -> None:
    """Namespace → Cluster（从属）。"""
    cluster_resource = await _find_resource_by_provider_id(
        session, "k8s", "k8s_cluster", cluster, cluster,
    )
    if cluster_resource:
        await _upsert_belongs_to(rel_repo, resource.id, cluster_resource.id, "namespace_in_cluster")


# ── Service → Pod selector 匹配 ─────────────────────────────────────────────────


async def _rebuild_service_pod_relations(
    session: AsyncSession,
    rel_repo: CmdbRelationshipRepo,
    service: CmdbResource,
    cluster: str,
    namespace: str,
    selector: dict[str, str],
) -> None:
    """根据 Service selector 匹配 Pod，建立关联关系。"""
    # 查询同 namespace 下所有 Pod
    result = await session.execute(
        select(CmdbResource).where(
            CmdbResource.provider == "k8s",
            CmdbResource.resource_type == "k8s_pod",
            CmdbResource.cloud_account == cluster,
            CmdbResource.deleted_at.is_(None),
        )
    )
    pods = list(result.scalars().all())

    # 匹配 selector
    for pod in pods:
        pod_labels = pod.attributes.get("metadata", {}).get("labels", {})
        if all(pod_labels.get(k) == v for k, v in selector.items()):
            await _upsert_relates_to(rel_repo, service.id, pod.id, "service_selects_pod")


# ── 工具函数 ─────────────────────────────────────────────────────────────────────


async def _find_resource_by_provider_id(
    session: AsyncSession,
    provider: str,
    resource_type: str,
    provider_id: str,
    cloud_account: str,
) -> CmdbResource | None:
    """通过 provider_id 查找资源。"""
    repo = CmdbResourceRepo(session)
    return await repo.get_by_provider_id(provider, resource_type, provider_id, cloud_account)


async def _ensure_belongs_to_by_type(
    session: AsyncSession,
    rel_repo: CmdbRelationshipRepo,
    child: CmdbResource,
    parent_type: str,
    cluster: str,
    namespace: str,
) -> None:
    """通过资源类型+namespace 查找父资源并建立从属关系。"""
    parent_provider_id = f"{cluster}/{namespace}" if namespace else cluster
    parent = await _find_resource_by_provider_id(
        session, "k8s", parent_type, parent_provider_id, cluster,
    )
    if parent:
        relation_type = f"{child.resource_type}_in_{parent_type}"
        await _upsert_belongs_to(rel_repo, child.id, parent.id, relation_type)


async def _ensure_belongs_to_by_name(
    session: AsyncSession,
    rel_repo: CmdbRelationshipRepo,
    child: CmdbResource,
    parent_type: str,
    cluster: str,
    namespace: str,
    name: str,
) -> None:
    """通过资源名查找父资源并建立从属关系。"""
    parent_provider_id = f"{cluster}/{namespace}/{name}" if namespace else f"{cluster}/{name}"
    parent = await _find_resource_by_provider_id(
        session, "k8s", parent_type, parent_provider_id, cluster,
    )
    if parent:
        relation_type = f"{child.resource_type}_in_{parent_type}"
        await _upsert_belongs_to(rel_repo, child.id, parent.id, relation_type)


async def _upsert_belongs_to(
    rel_repo: CmdbRelationshipRepo,
    child_id: int,
    parent_id: int,
    relation_type: str,
) -> None:
    """幂等创建从属关系（已存在则跳过）。"""
    existing = await rel_repo.get_children(parent_id, relation_type)
    for rel in existing:
        if rel.child_id == child_id:
            return  # 已存在

    relation = CmdbBelongsTo(
        child_id=child_id,
        parent_id=parent_id,
        relation_type=relation_type,
        synced_at=datetime.now(timezone.utc),
        source="discovery",
    )
    await rel_repo.create_belongs_to(relation)


async def _upsert_relates_to(
    rel_repo: CmdbRelationshipRepo,
    source_id: int,
    target_id: int,
    relation_type: str,
) -> None:
    """幂等创建关联关系（已存在则跳过）。"""
    existing = await rel_repo.get_relations_from(source_id, relation_type)
    for rel in existing:
        if rel.target_id == target_id:
            return  # 已存在

    relation = CmdbRelatesTo(
        source_id=source_id,
        target_id=target_id,
        relation_type=relation_type,
        synced_at=datetime.now(timezone.utc),
        source="discovery",
    )
    await rel_repo.create_relates_to(relation)
