"""CMDB 云资源关系重建引擎。

在云资源 Upsert 后，按已知资源类型的字段→provider_id 映射重建实例关系边。
Delete 时调用 remove_resource_edges（复用 K8s 侧函数）清边。

当前已覆盖：aliyun_ecs（→ VPC / VSwitch / SecurityGroup）
待扩展：SLB、RDS、Redis 等（需对应适配器先产出资源后，在此追加 elif 块）。

策略：从属边对子节点整包替换（先删后建）；关联边按语义槽位替换。
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
    """重建单个云资源的关系边（Upsert 后调用）。"""
    model_repo = CmdbModelRepo(session)
    model = await model_repo.get_model(resource.model_id)
    if model is None:
        return

    rel_repo = CmdbRelationshipRepo(session)
    res_repo = CmdbResourceRepo(session)

    # 先清空该资源的出边（整包替换）
    await rel_repo.delete_belongs_to_by_child(resource.id)
    await rel_repo.delete_relates_to_by_source(resource.id)

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
    """ECS: VPC（从属）、VSwitch（从属）、SecurityGroup（关联）。"""
    fields = resource.fields or {}
    provider = message.provider
    account = message.cloud_account
    now = datetime.now(timezone.utc)

    # --- ECS → VPC（belongs_to）---
    vpc_id = fields.get("vpc_id")
    if vpc_id:
        vpc_model = await model_repo.get_model_by_code("aliyun_vpc")
        if vpc_model:
            vpc = await res_repo.get_by_provider_id(vpc_model.id, provider, vpc_id, account)
            if vpc:
                await rel_repo.create_belongs_to(CmdbBelongsTo(
                    child_id=resource.id,
                    parent_id=vpc.id,
                    description=DESC_DEPLOYED_IN,
                    synced_at=now,
                    source="discovery",
                ))

    # --- ECS → VSwitch（belongs_to，来自采集器的 parent 提示）---
    if message.parent_provider_id and message.parent_resource_type:
        vswitch_model = await model_repo.get_model_by_code(message.parent_resource_type)
        if vswitch_model:
            vswitch = await res_repo.get_by_provider_id(
                vswitch_model.id, provider, message.parent_provider_id, account,
            )
            if vswitch:
                await rel_repo.create_belongs_to(CmdbBelongsTo(
                    child_id=resource.id,
                    parent_id=vswitch.id,
                    description=DESC_DEPLOYED_IN,
                    synced_at=now,
                    source="discovery",
                ))

    # --- ECS → SecurityGroup（relates_to）---
    sg_ids = fields.get("security_group_ids") or []
    if sg_ids:
        sg_model = await model_repo.get_model_by_code("aliyun_security_group")
        if sg_model:
            for sg_id in sg_ids:
                sg = await res_repo.get_by_provider_id(sg_model.id, provider, sg_id, account)
                if sg:
                    await rel_repo.create_relates_to(CmdbRelatesTo(
                        source_id=resource.id,
                        target_id=sg.id,
                        description=DESC_BIND_SG,
                        synced_at=now,
                        source="discovery",
                    ))
