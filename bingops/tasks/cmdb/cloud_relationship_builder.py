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
    # 后续扩展：elif model.code == "aliyun_slb": ...
    # 后续扩展：elif model.code == "aliyun_rds": ...


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
