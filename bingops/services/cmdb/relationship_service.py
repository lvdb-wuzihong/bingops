"""CMDB 关系管理服务。"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from bingops.core.exceptions import ConflictError, NotFoundError
from bingops.models.cmdb.relationship import CmdbBelongsTo, CmdbRelatesTo
from bingops.repositories.cmdb.model_repo import CmdbModelRepo
from bingops.repositories.cmdb.relationship_repo import CmdbRelationshipRepo
from bingops.repositories.cmdb.resource_repo import CmdbResourceRepo
from bingops.schemas.cmdb.relationship import BelongsToCreate, RelatesToCreate

logger = logging.getLogger(f"bingops.{__name__}")

# 拓扑子图节点数硬上限：防账号根等高扇出节点一次拉爆响应
TOPOLOGY_MAX_NODES = 500
# 单跳新增邻居上限：超出说明撞到了账号根这类枢纽节点，
# 整跳丢弃置 truncated（宁缺不完整子图，不出带残缺邻居的误导图）
TOPOLOGY_MAX_FANOUT = 300


async def _ensure_resource_exists(session: AsyncSession, resource_id: int) -> None:
    """校验资源是否存在。"""
    resource_repo = CmdbResourceRepo(session)
    resource = await resource_repo.get_by_id(resource_id)
    if resource is None:
        raise NotFoundError("CmdbResource", str(resource_id))


# ── 从属关系 ────────────────────────────────────────────────────────────────────


async def add_belongs_to(session: AsyncSession, payload: BelongsToCreate) -> CmdbBelongsTo:
    """创建从属关系（child → parent）。"""
    if payload.child_id == payload.parent_id:
        raise ConflictError("CmdbBelongsTo", "child_id and parent_id cannot be the same")

    await _ensure_resource_exists(session, payload.child_id)
    await _ensure_resource_exists(session, payload.parent_id)

    repo = CmdbRelationshipRepo(session)
    relation = CmdbBelongsTo(
        child_id=payload.child_id,
        parent_id=payload.parent_id,
        description=payload.description,
        source="manual",
    )
    relation = await repo.create_belongs_to(relation)
    await session.commit()

    logger.info(
        "CMDB belongs_to relation created",
        extra={"child_id": payload.child_id, "parent_id": payload.parent_id},
    )
    return relation


async def remove_belongs_to(session: AsyncSession, relation_id: int) -> None:
    """删除从属关系。"""
    repo = CmdbRelationshipRepo(session)
    relation = await repo.delete_belongs_to(relation_id)
    if relation is None:
        raise NotFoundError("CmdbBelongsTo", str(relation_id))
    await session.commit()
    logger.info("CMDB belongs_to relation deleted", extra={"relation_id": relation_id})


async def get_children(
    session: AsyncSession, resource_id: int, description: str | None = None,
) -> list[CmdbBelongsTo]:
    """获取某资源的所有子资源关系。"""
    repo = CmdbRelationshipRepo(session)
    return await repo.get_children(resource_id, description)


async def get_parents(
    session: AsyncSession, resource_id: int, description: str | None = None,
) -> list[CmdbBelongsTo]:
    """获取某资源的所有父资源关系。"""
    repo = CmdbRelationshipRepo(session)
    return await repo.get_parents(resource_id, description)


# ── 关联关系 ────────────────────────────────────────────────────────────────────


async def add_relates_to(session: AsyncSession, payload: RelatesToCreate) -> CmdbRelatesTo:
    """创建关联关系（source → target）。"""
    if payload.source_id == payload.target_id:
        raise ConflictError("CmdbRelatesTo", "source_id and target_id cannot be the same")

    await _ensure_resource_exists(session, payload.source_id)
    await _ensure_resource_exists(session, payload.target_id)

    repo = CmdbRelationshipRepo(session)
    relation = CmdbRelatesTo(
        source_id=payload.source_id,
        target_id=payload.target_id,
        description=payload.description,
        attributes=payload.attributes,
        source="manual",
    )
    relation = await repo.create_relates_to(relation)
    await session.commit()

    logger.info(
        "CMDB relates_to relation created",
        extra={"source_id": payload.source_id, "target_id": payload.target_id},
    )
    return relation


async def remove_relates_to(session: AsyncSession, relation_id: int) -> None:
    """删除关联关系。"""
    repo = CmdbRelationshipRepo(session)
    relation = await repo.delete_relates_to(relation_id)
    if relation is None:
        raise NotFoundError("CmdbRelatesTo", str(relation_id))
    await session.commit()
    logger.info("CMDB relates_to relation deleted", extra={"relation_id": relation_id})


async def get_relations_from(
    session: AsyncSession, resource_id: int, description: str | None = None,
) -> list[CmdbRelatesTo]:
    """获取从某资源出发的所有关联关系。"""
    repo = CmdbRelationshipRepo(session)
    return await repo.get_relations_from(resource_id, description)


async def get_relations_to(
    session: AsyncSession, resource_id: int, description: str | None = None,
) -> list[CmdbRelatesTo]:
    """获取指向某资源的所有关联关系。"""
    repo = CmdbRelationshipRepo(session)
    return await repo.get_relations_to(resource_id, description)


# ── 拓扑子图 ──────────────────────────────────────────────────────────────


async def get_topology(
    session: AsyncSession, resource_id: int, depth: int,
) -> dict:
    """以资源为中心双向 BFS 展开拓扑子图（nodes + edges 一次返回）。

    - 三种边全走：belongs_to 向上/向下、relates_to 双向
    - 节点只返渲染必需字段（不带 fields JSONB）
    - 节点数达 TOPOLOGY_MAX_NODES 后停止扩张并置 truncated
    - 边的两端必须都在节点集内（软删资源/截断丢弃的节点对应边不返）
    """
    resource_repo = CmdbResourceRepo(session)
    center = await resource_repo.get_by_id(resource_id)
    if center is None:
        raise NotFoundError("CmdbResource", str(resource_id))

    rel_repo = CmdbRelationshipRepo(session)
    visited: set[int] = {resource_id}
    frontier: list[int] = [resource_id]
    belongs_edges: dict[int, CmdbBelongsTo] = {}
    relates_edges: dict[int, CmdbRelatesTo] = {}
    truncated = False

    for hop in range(depth):
        if not frontier:
            break
        bt_batch = await rel_repo.list_belongs_to_involving(frontier)
        rt_batch = await rel_repo.list_relates_to_involving(frontier)
        next_frontier: list[int] = []

        def _visit(nid: int) -> None:
            if nid in visited or nid in next_frontier:
                return
            next_frontier.append(nid)

        for edge in bt_batch:
            _visit(edge.child_id)
            _visit(edge.parent_id)
        for edge in rt_batch:
            _visit(edge.source_id)
            _visit(edge.target_id)

        # 高扇出防御：本跳邻居超限则整跳丢弃（本跳边也不计入结果）
        if len(visited) + len(next_frontier) > TOPOLOGY_MAX_NODES or len(next_frontier) > TOPOLOGY_MAX_FANOUT:
            truncated = True
            logger.warning(
                "Topology expansion truncated at high fan-out hop",
                extra={
                    "resource_id": resource_id, "hop": hop + 1,
                    "new_neighbors": len(next_frontier),
                },
            )
            break

        for edge in bt_batch:
            belongs_edges[edge.id] = edge
        for edge in rt_batch:
            relates_edges[edge.id] = edge
        visited.update(next_frontier)
        frontier = next_frontier

    # 节点装载 + 模型 code/name 映射（模型总量小，一次全拉）
    resources = await resource_repo.list_by_ids(list(visited))
    nodes_by_id = {r.id: r for r in resources}
    model_repo = CmdbModelRepo(session)
    models = {m.id: m for m in await model_repo.list_models()}

    nodes = [
        {
            "id": r.id,
            "name": r.name,
            "model_id": r.model_id,
            "model_code": models[r.model_id].code if r.model_id in models else None,
            "model_name": models[r.model_id].name if r.model_id in models else None,
            "provider": r.provider,
            "status": r.status,
            "region": r.region,
            "is_center": r.id == resource_id,
        }
        for r in sorted(nodes_by_id.values(), key=lambda x: x.id)
    ]

    # 边只保留两端都在节点集内的（软删资源或截断丢弃的节点对应边不返）
    edges: list[dict] = [
        {
            "id": e.id,
            "relation_type": "belongs_to",
            "source_id": e.child_id,
            "target_id": e.parent_id,
            "description": e.description,
            "source": e.source,
        }
        for e in belongs_edges.values()
        if e.child_id in nodes_by_id and e.parent_id in nodes_by_id
    ]
    edges += [
        {
            "id": e.id,
            "relation_type": "relates_to",
            "source_id": e.source_id,
            "target_id": e.target_id,
            "description": e.description,
            "kind": e.kind,
            "source": e.source,
        }
        for e in relates_edges.values()
        if e.source_id in nodes_by_id and e.target_id in nodes_by_id
    ]

    logger.info(
        "CMDB topology subgraph built",
        extra={
            "resource_id": resource_id, "depth": depth,
            "node_count": len(nodes), "edge_count": len(edges),
            "truncated": truncated,
        },
    )
    return {
        "center_id": resource_id,
        "depth": depth,
        "truncated": truncated,
        "nodes": nodes,
        "edges": edges,
    }
