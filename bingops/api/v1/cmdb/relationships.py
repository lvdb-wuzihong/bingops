"""CMDB 关系管理 API 路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.api.dependencies import get_db_session, require_permission
from bingops.core.response import success_response
from bingops.models.user import User
from bingops.schemas.cmdb.relationship import (
    BelongsToCreate,
    BelongsToResponse,
    RelatesToCreate,
    RelatesToResponse,
)
from bingops.services.cmdb import relationship_service

router = APIRouter(prefix="/api/v1/cmdb", tags=["cmdb-relationships"])


# ── 从属关系 (Belongs To) ──────────────────────────────────────────────────────


@router.post("/belongs-to", status_code=201)
async def add_belongs_to(
    payload: BelongsToCreate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_resource:create"),
):
    """创建从属关系（child → parent）。"""
    relation = await relationship_service.add_belongs_to(session, payload)
    data = BelongsToResponse(
        id=relation.id,
        child_id=relation.child_id,
        parent_id=relation.parent_id,
        description=relation.description,
        synced_at=relation.synced_at,
        source=relation.source,
        created_at=relation.created_at,
    )
    return success_response(data=data.model_dump(mode="json"), message="Belongs-to relation created", http_status=201)


@router.delete("/belongs-to/{relation_id}")
async def remove_belongs_to(
    relation_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_resource:delete"),
):
    """删除从属关系。"""
    await relationship_service.remove_belongs_to(session, relation_id)
    return success_response(message="Belongs-to relation deleted")


@router.get("/resources/{resource_id}/children")
async def get_children(
    resource_id: int,
    description: str | None = None,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_resource:list"),
):
    """查询某资源的所有子资源（从属关系）。"""
    relations = await relationship_service.get_children(session, resource_id, description)
    items = [
        BelongsToResponse(
            id=r.id,
            child_id=r.child_id,
            parent_id=r.parent_id,
            description=r.description,
            synced_at=r.synced_at,
            source=r.source,
            created_at=r.created_at,
        ).model_dump(mode="json")
        for r in relations
    ]
    return success_response(data=items)


@router.get("/resources/{resource_id}/parents")
async def get_parents(
    resource_id: int,
    description: str | None = None,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_resource:list"),
):
    """查询某资源的所有父资源（从属关系）。"""
    relations = await relationship_service.get_parents(session, resource_id, description)
    items = [
        BelongsToResponse(
            id=r.id,
            child_id=r.child_id,
            parent_id=r.parent_id,
            description=r.description,
            synced_at=r.synced_at,
            source=r.source,
            created_at=r.created_at,
        ).model_dump(mode="json")
        for r in relations
    ]
    return success_response(data=items)


# ── 关联关系 (Relates To) ──────────────────────────────────────────────────────


@router.post("/relates-to", status_code=201)
async def add_relates_to(
    payload: RelatesToCreate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_resource:create"),
):
    """创建关联关系（source → target）。"""
    relation = await relationship_service.add_relates_to(session, payload)
    data = RelatesToResponse(
        id=relation.id,
        source_id=relation.source_id,
        target_id=relation.target_id,
        description=relation.description,
        attributes=relation.attributes,
        synced_at=relation.synced_at,
        source=relation.source,
        created_at=relation.created_at,
    )
    return success_response(data=data.model_dump(mode="json"), message="Relates-to relation created", http_status=201)


@router.delete("/relates-to/{relation_id}")
async def remove_relates_to(
    relation_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_resource:delete"),
):
    """删除关联关系。"""
    await relationship_service.remove_relates_to(session, relation_id)
    return success_response(message="Relates-to relation deleted")


@router.get("/resources/{resource_id}/relations-from")
async def get_relations_from(
    resource_id: int,
    description: str | None = None,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_resource:list"),
):
    """查询从某资源出发的所有关联关系。"""
    relations = await relationship_service.get_relations_from(session, resource_id, description)
    items = [
        RelatesToResponse(
            id=r.id,
            source_id=r.source_id,
            target_id=r.target_id,
            description=r.description,
            attributes=r.attributes,
            synced_at=r.synced_at,
            source=r.source,
            created_at=r.created_at,
        ).model_dump(mode="json")
        for r in relations
    ]
    return success_response(data=items)


@router.get("/resources/{resource_id}/relations-to")
async def get_relations_to(
    resource_id: int,
    description: str | None = None,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_resource:list"),
):
    """查询指向某资源的所有关联关系。"""
    relations = await relationship_service.get_relations_to(session, resource_id, description)
    items = [
        RelatesToResponse(
            id=r.id,
            source_id=r.source_id,
            target_id=r.target_id,
            description=r.description,
            attributes=r.attributes,
            synced_at=r.synced_at,
            source=r.source,
            created_at=r.created_at,
        ).model_dump(mode="json")
        for r in relations
    ]
    return success_response(data=items)


# ── 拓扑子图 ──────────────────────────────────────────────────────────────


@router.get("/resources/{resource_id}/topology")
async def get_topology(
    resource_id: int,
    depth: int = Query(default=2, ge=1, le=3, description="展开跳数，硬顶 3"),
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_resource:list"),
):
    """查询以资源为中心双向展开的拓扑子图（nodes + edges 一次返回）。

    节点为瘦身负载（无 fields JSONB）；边带 relation_type/description/kind，
    belongs_to 方向为 source=child → target=parent。节点数达上限后
    truncated=true，前端可提示缩小 depth。
    """
    data = await relationship_service.get_topology(session, resource_id, depth)
    return success_response(data=data)
