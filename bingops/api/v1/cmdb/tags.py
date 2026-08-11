"""CMDB 标签管理 API 路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.api.dependencies import get_db_session, require_permission
from bingops.core.response import paginated_response, success_response
from bingops.models.user import User
from bingops.schemas.cmdb.tag import (
    ResourceTagCreate,
    ResourceTagResponse,
    TagDefinitionCreate,
    TagDefinitionResponse,
    TagDefinitionUpdate,
)
from bingops.services.cmdb import tag_service

router = APIRouter(prefix="/api/v1/cmdb/tags", tags=["cmdb-tags"])


# ── 标签定义 ────────────────────────────────────────────────────────────────────


@router.get("")
async def list_tag_definitions(
    category: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_tag:list"),
):
    """分页查询标签定义列表。"""
    tag_defs, total = await tag_service.list_tag_definitions(
        session, category=category, page=page, page_size=page_size,
    )
    items = [
        TagDefinitionResponse(
            id=td.id,
            tag_key=td.tag_key,
            name=td.name,
            description=td.description,
            category=td.category,
            value_type=td.value_type,
            allowed_values=td.allowed_values,
            editable=td.editable,
            created_at=td.created_at,
            updated_at=td.updated_at,
        ).model_dump(mode="json")
        for td in tag_defs
    ]
    return paginated_response(items, total, page, page_size)


@router.post("", status_code=201)
async def create_tag_definition(
    payload: TagDefinitionCreate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_tag:create"),
):
    """创建标签定义。"""
    tag_def = await tag_service.create_tag_definition(session, payload)
    data = TagDefinitionResponse(
        id=tag_def.id,
        tag_key=tag_def.tag_key,
        name=tag_def.name,
        description=tag_def.description,
        category=tag_def.category,
        value_type=tag_def.value_type,
        allowed_values=tag_def.allowed_values,
        editable=tag_def.editable,
        created_at=tag_def.created_at,
        updated_at=tag_def.updated_at,
    )
    return success_response(data=data.model_dump(mode="json"), message="Tag definition created", http_status=201)


@router.get("/{tag_def_id}")
async def get_tag_definition(
    tag_def_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_tag:list"),
):
    """获取标签定义详情。"""
    tag_def = await tag_service.get_tag_definition(session, tag_def_id)
    data = TagDefinitionResponse(
        id=tag_def.id,
        tag_key=tag_def.tag_key,
        name=tag_def.name,
        description=tag_def.description,
        category=tag_def.category,
        value_type=tag_def.value_type,
        allowed_values=tag_def.allowed_values,
        editable=tag_def.editable,
        created_at=tag_def.created_at,
        updated_at=tag_def.updated_at,
    )
    return success_response(data=data.model_dump(mode="json"))


@router.put("/{tag_def_id}")
async def update_tag_definition(
    tag_def_id: int,
    payload: TagDefinitionUpdate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_tag:update"),
):
    """更新标签定义。"""
    tag_def = await tag_service.update_tag_definition(session, tag_def_id, payload)
    data = TagDefinitionResponse(
        id=tag_def.id,
        tag_key=tag_def.tag_key,
        name=tag_def.name,
        description=tag_def.description,
        category=tag_def.category,
        value_type=tag_def.value_type,
        allowed_values=tag_def.allowed_values,
        editable=tag_def.editable,
        created_at=tag_def.created_at,
        updated_at=tag_def.updated_at,
    )
    return success_response(data=data.model_dump(mode="json"))


@router.delete("/{tag_def_id}")
async def delete_tag_definition(
    tag_def_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_tag:delete"),
):
    """删除标签定义（系统标签不可删）。"""
    await tag_service.delete_tag_definition(session, tag_def_id)
    return success_response(message="Tag definition deleted")


# ── 资源标签 ────────────────────────────────────────────────────────────────────


@router.get("/resources/{resource_id}")
async def get_resource_tags(
    resource_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_tag:list"),
):
    """获取某资源的所有标签。"""
    tags = await tag_service.get_resource_tags(session, resource_id)
    items = [
        ResourceTagResponse(
            id=t.id,
            resource_id=t.resource_id,
            tag_key=t.tag_key,
            tag_value=t.tag_value,
            source=t.source,
            raw_key=t.raw_key,
            synced_at=t.synced_at,
            operator=t.operator,
            created_at=t.created_at,
            updated_at=t.updated_at,
        ).model_dump(mode="json")
        for t in tags
    ]
    return success_response(data=items)


@router.post("/resources", status_code=201)
async def add_resource_tag(
    payload: ResourceTagCreate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_tag:create"),
):
    """为资源打标签。"""
    tag = await tag_service.add_resource_tag(session, payload)
    data = ResourceTagResponse(
        id=tag.id,
        resource_id=tag.resource_id,
        tag_key=tag.tag_key,
        tag_value=tag.tag_value,
        source=tag.source,
        raw_key=tag.raw_key,
        synced_at=tag.synced_at,
        operator=tag.operator,
        created_at=tag.created_at,
        updated_at=tag.updated_at,
    )
    return success_response(data=data.model_dump(mode="json"), message="Resource tag added", http_status=201)


@router.delete("/resources/{resource_id}/{tag_key}")
async def remove_resource_tag(
    resource_id: int,
    tag_key: str,
    source: str | None = None,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_tag:delete"),
):
    """移除资源的标签。"""
    await tag_service.remove_resource_tag(session, resource_id, tag_key, source)
    return success_response(message="Resource tag removed")


@router.get("/search/by-tag")
async def find_resources_by_tag(
    tag_key: str,
    tag_value: str | None = None,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_tag:list"),
):
    """按标签查询资源 ID 列表。"""
    resource_ids = await tag_service.find_resources_by_tag(session, tag_key, tag_value)
    return success_response(data={"resource_ids": resource_ids})
