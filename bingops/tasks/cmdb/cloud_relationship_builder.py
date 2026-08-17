"""CMDB 云资源关系重建引擎。

在云资源 Upsert 后，按已知资源类型的字段→provider_id 映射重建实例关系边。
Delete 时调用 remove_resource_edges（复用 K8s 侧函数）清边。

当前已覆盖：aliyun_ecs（→ VPC / VSwitch / SecurityGroup）
待扩展：SLB、RDS、Redis 等（需对应适配器先产出资源后，在此追加 elif 块）。

策略：先解析预期边、与当前库内边做 diff，仅在有差异时才清空重建（避免每轮无效 I/O）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from bingops.models.cmdb.relationship import CmdbBelongsTo, CmdbRelatesTo
from bingops.models.cmdb.resource import CmdbResource
from bingops.repositories.cmdb.model_repo import CmdbModelRepo
from bingops.repositories.cmdb.relationship_repo import CmdbRelationshipRepo
from bingops.repositories.cmdb.resource_repo import CmdbResourceRepo
from bingops.schemas.cmdb.kafka_messages import CloudResourceMessage

logger = logging.getLogger(f"bingops.{__name__}")

# 边语义描述（对齐 cmdb_model_relations.relation_name 前端展示用）
DESC_DEPLOYED_IN = "部署于"
DESC_BIND_SG = "绑定安全组"
DESC_BIND_ECS = "绑定"
DESC_NETWORK_BELONG = "网络归属"
DESC_LB_BACKEND = "负载均衡后端"
DESC_SG_BACKEND = "服务器组后端"
DESC_BIND_EIP = "绑定 EIP"
DESC_ACCOUNT_BELONG = "账号归属"
DESC_MOUNT_ECS = "挂载于"


async def rebuild_cloud_relationships(
    session: AsyncSession,
    resource: CmdbResource,
    message: CloudResourceMessage,
) -> None:
    """重建单个云资源的关系边（Upsert 后调用，内置 diff 跳过无变更）。"""
    model_repo = CmdbModelRepo(session)
    model = await model_repo.get_model(resource.model_id)
    if model is None:
        return

    rel_repo = CmdbRelationshipRepo(session)
    res_repo = CmdbResourceRepo(session)

    if model.code == "aliyun_ecs":
        await _rebuild_ecs_edges(session, rel_repo, res_repo, model_repo, resource, message)
    elif model.code == "aliyun_eip":
        await _rebuild_eip_edges(session, rel_repo, res_repo, model_repo, resource, message)
    elif model.code == "aliyun_clb":
        await _rebuild_clb_edges(session, rel_repo, res_repo, model_repo, resource, message)
    elif model.code == "aliyun_nlb":
        await _rebuild_nlb_edges(session, rel_repo, res_repo, model_repo, resource, message)
    elif model.code == "aliyun_nat_gateway":
        await _rebuild_nat_edges(session, rel_repo, res_repo, model_repo, resource, message)
    elif model.code == "aliyun_disk":
        await _rebuild_disk_edges(session, rel_repo, res_repo, model_repo, resource, message)
    elif model.code == "aliyun_oss":
        # OSS → 云账号 belongs_to（账号归属），复用通用 parent 重建
        await _rebuild_parent_edge(
            session, rel_repo, res_repo, model_repo, resource, message,
            description=DESC_ACCOUNT_BELONG,
        )
    else:
        # 通用 parent 关系重建（VSwitch → VPC 等，无复杂多边场景）
        await _rebuild_parent_edge(session, rel_repo, res_repo, model_repo, resource, message)


# ── 通用 parent 关系重建 ───────────────────────────────────────────────────────


async def _rebuild_parent_edge(
    session: AsyncSession,
    rel_repo: CmdbRelationshipRepo,
    res_repo: CmdbResourceRepo,
    model_repo: CmdbModelRepo,
    resource: CmdbResource,
    message: CloudResourceMessage,
    description: str = DESC_DEPLOYED_IN,
) -> None:
    """通用：根据 message.parent_provider_id 建 belongs_to 边（diff 跳过无变更）。"""
    if not message.parent_provider_id or not message.parent_resource_type:
        return

    provider = message.provider
    account = message.cloud_account

    # 查找父资源
    parent_model = await model_repo.get_model_by_code(message.parent_resource_type)
    if parent_model is None:
        return
    parent = await res_repo.get_by_provider_id(
        parent_model.id, provider, message.parent_provider_id, account,
    )
    if parent is None:
        return

    # 查现有边
    current_parents = await rel_repo.get_parents(resource.id)
    current_parent_ids = {p.parent_id for p in current_parents}

    if parent.id in current_parent_ids:
        return  # 已存在，跳过

    # 清旧建新
    await rel_repo.delete_belongs_to_by_child(resource.id)
    now = datetime.now(timezone.utc)
    await rel_repo.create_belongs_to(CmdbBelongsTo(
        child_id=resource.id,
        parent_id=parent.id,
        description=description,
        synced_at=now,
        source="discovery",
    ))


# ── EIP 关系重建 ────────────────────────────────────────────────────────────


async def _rebuild_eip_edges(
    session: AsyncSession,
    rel_repo: CmdbRelationshipRepo,
    res_repo: CmdbResourceRepo,
    model_repo: CmdbModelRepo,
    resource: CmdbResource,
    message: CloudResourceMessage,
) -> None:
    """EIP: 绑定 ECS 实例时建 relates_to 边（diff 跳过无变更）。"""
    fields = resource.fields or {}
    expected_relate_to_ids: set[int] = set()

    if fields.get("bind_instance_type") == "EcsInstance":
        ecs_id = fields.get("bind_instance_id")
        if ecs_id:
            ecs_model = await model_repo.get_model_by_code("aliyun_ecs")
            if ecs_model:
                ecs = await res_repo.get_by_provider_id(
                    ecs_model.id, message.provider, ecs_id, message.cloud_account,
                )
                if ecs:
                    expected_relate_to_ids.add(ecs.id)

    current_relations = await rel_repo.get_relations_from(resource.id)
    current_relate_ids = {r.target_id for r in current_relations}
    if expected_relate_to_ids == current_relate_ids:
        return

    await rel_repo.delete_relates_to_by_source(resource.id)
    now = datetime.now(timezone.utc)
    for target_id in expected_relate_to_ids:
        await rel_repo.create_relates_to(CmdbRelatesTo(
            source_id=resource.id,
            target_id=target_id,
            description=DESC_BIND_ECS,
            synced_at=now,
            source="discovery",
        ))


# ── CLB / NLB 关系重建 ──────────────────────────────────────────────────────


async def _rebuild_clb_edges(
    session: AsyncSession,
    rel_repo: CmdbRelationshipRepo,
    res_repo: CmdbResourceRepo,
    model_repo: CmdbModelRepo,
    resource: CmdbResource,
    message: CloudResourceMessage,
) -> None:
    """CLB: → VPC belongs_to（网络归属）+ 后端 ECS relates_to（负载均衡后端）。"""
    await _rebuild_lb_edges(
        session, rel_repo, res_repo, model_repo, resource, message, DESC_LB_BACKEND,
    )


async def _rebuild_nlb_edges(
    session: AsyncSession,
    rel_repo: CmdbRelationshipRepo,
    res_repo: CmdbResourceRepo,
    model_repo: CmdbModelRepo,
    resource: CmdbResource,
    message: CloudResourceMessage,
) -> None:
    """NLB: → VPC belongs_to（网络归属）+ 服务器组后端 ECS relates_to（服务器组后端）。"""
    await _rebuild_lb_edges(
        session, rel_repo, res_repo, model_repo, resource, message, DESC_SG_BACKEND,
    )


async def _rebuild_lb_edges(
    session: AsyncSession,
    rel_repo: CmdbRelationshipRepo,
    res_repo: CmdbResourceRepo,
    model_repo: CmdbModelRepo,
    resource: CmdbResource,
    message: CloudResourceMessage,
    backend_desc: str,
) -> None:
    """负载均衡通用：→ VPC belongs_to（网络归属）+ 后端 ECS relates_to（diff 跳过无变更）。"""
    fields = resource.fields or {}
    provider = message.provider
    account = message.cloud_account

    expected_parent_ids: set[int] = set()
    vpc_id = fields.get("vpc_id")
    if vpc_id:
        vpc_model = await model_repo.get_model_by_code("aliyun_vpc")
        if vpc_model:
            vpc = await res_repo.get_by_provider_id(vpc_model.id, provider, vpc_id, account)
            if vpc:
                expected_parent_ids.add(vpc.id)

    expected_relate_to_ids: set[int] = set()
    ecs_model = await model_repo.get_model_by_code("aliyun_ecs")
    if ecs_model:
        for ecs_id in fields.get("_backend_ecs_ids") or []:
            ecs = await res_repo.get_by_provider_id(ecs_model.id, provider, ecs_id, account)
            if ecs:
                expected_relate_to_ids.add(ecs.id)

    current_parents = await rel_repo.get_parents(resource.id)
    current_parent_ids = {p.parent_id for p in current_parents}
    current_relations = await rel_repo.get_relations_from(resource.id)
    current_relate_ids = {r.target_id for r in current_relations}

    if expected_parent_ids == current_parent_ids and expected_relate_to_ids == current_relate_ids:
        return

    await rel_repo.delete_belongs_to_by_child(resource.id)
    await rel_repo.delete_relates_to_by_source(resource.id)
    now = datetime.now(timezone.utc)

    for parent_id in expected_parent_ids:
        await rel_repo.create_belongs_to(CmdbBelongsTo(
            child_id=resource.id,
            parent_id=parent_id,
            description=DESC_NETWORK_BELONG,
            synced_at=now,
            source="discovery",
        ))

    for ecs_id in expected_relate_to_ids:
        await rel_repo.create_relates_to(CmdbRelatesTo(
            source_id=resource.id,
            target_id=ecs_id,
            description=backend_desc,
            synced_at=now,
            source="discovery",
        ))


# ── NAT 网关关系重建 ──────────────────────────────────────────────────────


async def _rebuild_nat_edges(
    session: AsyncSession,
    rel_repo: CmdbRelationshipRepo,
    res_repo: CmdbResourceRepo,
    model_repo: CmdbModelRepo,
    resource: CmdbResource,
    message: CloudResourceMessage,
) -> None:
    """NAT: → VPC belongs_to（网络归属）+ 绑定 EIP relates_to（绑定 EIP）。"""
    fields = resource.fields or {}
    provider = message.provider
    account = message.cloud_account

    expected_parent_ids: set[int] = set()
    vpc_id = fields.get("vpc_id")
    if vpc_id:
        vpc_model = await model_repo.get_model_by_code("aliyun_vpc")
        if vpc_model:
            vpc = await res_repo.get_by_provider_id(vpc_model.id, provider, vpc_id, account)
            if vpc:
                expected_parent_ids.add(vpc.id)

    expected_relate_to_ids: set[int] = set()
    eip_model = await model_repo.get_model_by_code("aliyun_eip")
    if eip_model:
        for eip_id in fields.get("eip_ids") or []:
            eip = await res_repo.get_by_provider_id(eip_model.id, provider, eip_id, account)
            if eip:
                expected_relate_to_ids.add(eip.id)

    current_parents = await rel_repo.get_parents(resource.id)
    current_parent_ids = {p.parent_id for p in current_parents}
    current_relations = await rel_repo.get_relations_from(resource.id)
    current_relate_ids = {r.target_id for r in current_relations}

    if expected_parent_ids == current_parent_ids and expected_relate_to_ids == current_relate_ids:
        return

    await rel_repo.delete_belongs_to_by_child(resource.id)
    await rel_repo.delete_relates_to_by_source(resource.id)
    now = datetime.now(timezone.utc)

    for parent_id in expected_parent_ids:
        await rel_repo.create_belongs_to(CmdbBelongsTo(
            child_id=resource.id,
            parent_id=parent_id,
            description=DESC_NETWORK_BELONG,
            synced_at=now,
            source="discovery",
        ))

    for eip_id in expected_relate_to_ids:
        await rel_repo.create_relates_to(CmdbRelatesTo(
            source_id=resource.id,
            target_id=eip_id,
            description=DESC_BIND_EIP,
            synced_at=now,
            source="discovery",
        ))


# ── 云盘关系重建 ───────────────────────────────────────────────────────


async def _rebuild_disk_edges(
    session: AsyncSession,
    rel_repo: CmdbRelationshipRepo,
    res_repo: CmdbResourceRepo,
    model_repo: CmdbModelRepo,
    resource: CmdbResource,
    message: CloudResourceMessage,
) -> None:
    """云盘: → 账号 belongs_to（账号归属）+ 挂载 ECS relates_to（挂载于）。"""
    fields = resource.fields or {}
    provider = message.provider
    account = message.cloud_account

    # → 云账号（账号根节点存在时才建边，不存在则静默跳过）
    expected_parent_ids: set[int] = set()
    if message.parent_provider_id and message.parent_resource_type:
        parent_model = await model_repo.get_model_by_code(message.parent_resource_type)
        if parent_model:
            parent = await res_repo.get_by_provider_id(
                parent_model.id, provider, message.parent_provider_id, account,
            )
            if parent:
                expected_parent_ids.add(parent.id)

    # → 挂载的 ECS
    expected_relate_to_ids: set[int] = set()
    ecs_id = fields.get("instance_id")
    if ecs_id:
        ecs_model = await model_repo.get_model_by_code("aliyun_ecs")
        if ecs_model:
            ecs = await res_repo.get_by_provider_id(ecs_model.id, provider, ecs_id, account)
            if ecs:
                expected_relate_to_ids.add(ecs.id)

    current_parents = await rel_repo.get_parents(resource.id)
    current_parent_ids = {p.parent_id for p in current_parents}
    current_relations = await rel_repo.get_relations_from(resource.id)
    current_relate_ids = {r.target_id for r in current_relations}

    if expected_parent_ids == current_parent_ids and expected_relate_to_ids == current_relate_ids:
        return

    await rel_repo.delete_belongs_to_by_child(resource.id)
    await rel_repo.delete_relates_to_by_source(resource.id)
    now = datetime.now(timezone.utc)

    for parent_id in expected_parent_ids:
        await rel_repo.create_belongs_to(CmdbBelongsTo(
            child_id=resource.id,
            parent_id=parent_id,
            description=DESC_ACCOUNT_BELONG,
            synced_at=now,
            source="discovery",
        ))

    for target_id in expected_relate_to_ids:
        await rel_repo.create_relates_to(CmdbRelatesTo(
            source_id=resource.id,
            target_id=target_id,
            description=DESC_MOUNT_ECS,
            synced_at=now,
            source="discovery",
        ))


# ── ECS 关系重建 ────────────────────────────────────────────────────────────────


async def _rebuild_ecs_edges(
    session: AsyncSession,
    rel_repo: CmdbRelationshipRepo,
    res_repo: CmdbResourceRepo,
    model_repo: CmdbModelRepo,
    resource: CmdbResource,
    message: CloudResourceMessage,
) -> None:
    """ECS: 先 diff 再决定是否重建（避免每轮无变更时删了又建）。"""
    fields = resource.fields or {}
    provider = message.provider
    account = message.cloud_account

    # ── 解析预期边 ──
    expected_parent_ids: set[int] = set()
    expected_relate_to_ids: set[int] = set()

    # ECS → VPC
    vpc_id = fields.get("vpc_id")
    if vpc_id:
        vpc_model = await model_repo.get_model_by_code("aliyun_vpc")
        if vpc_model:
            vpc = await res_repo.get_by_provider_id(vpc_model.id, provider, vpc_id, account)
            if vpc:
                expected_parent_ids.add(vpc.id)

    # ECS → VSwitch
    if message.parent_provider_id and message.parent_resource_type:
        vswitch_model = await model_repo.get_model_by_code(message.parent_resource_type)
        if vswitch_model:
            vswitch = await res_repo.get_by_provider_id(
                vswitch_model.id, provider, message.parent_provider_id, account,
            )
            if vswitch:
                expected_parent_ids.add(vswitch.id)

    # ECS → SecurityGroup
    sg_ids = fields.get("security_group_ids") or []
    if sg_ids:
        sg_model = await model_repo.get_model_by_code("aliyun_security_group")
        if sg_model:
            for sg_id in sg_ids:
                sg = await res_repo.get_by_provider_id(sg_model.id, provider, sg_id, account)
                if sg:
                    expected_relate_to_ids.add(sg.id)

    # ── 查询当前库内边 ──
    current_parents = await rel_repo.get_parents(resource.id)
    current_parent_ids = {p.parent_id for p in current_parents}

    current_relations = await rel_repo.get_relations_from(resource.id)
    current_relate_ids = {r.target_id for r in current_relations}

    # ── diff：完全一致则跳过 ──
    if expected_parent_ids == current_parent_ids and expected_relate_to_ids == current_relate_ids:
        return

    # ── 有差异：清空 + 重建 ──
    await rel_repo.delete_belongs_to_by_child(resource.id)
    await rel_repo.delete_relates_to_by_source(resource.id)
    now = datetime.now(timezone.utc)

    for parent_id in expected_parent_ids:
        await rel_repo.create_belongs_to(CmdbBelongsTo(
            child_id=resource.id,
            parent_id=parent_id,
            description=DESC_DEPLOYED_IN,
            synced_at=now,
            source="discovery",
        ))

    for sg_id in expected_relate_to_ids:
        await rel_repo.create_relates_to(CmdbRelatesTo(
            source_id=resource.id,
            target_id=sg_id,
            description=DESC_BIND_SG,
            synced_at=now,
            source="discovery",
        ))
