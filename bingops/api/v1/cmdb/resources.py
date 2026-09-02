"""CMDB 资源管理 API 路由（v2 动态模型）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.api.dependencies import get_db_session, require_permission
from bingops.core.response import paginated_response, success_response
from bingops.models.user import User
from bingops.schemas.cmdb.resource import ResourceCreate, ResourceResponse, ResourceUpdate
from bingops.services.cmdb import resource_service

router = APIRouter(prefix="/api/v1/cmdb/resources", tags=["cmdb-resources"])


@router.get("")
async def list_resources(
    model_id: int | None = None,
    provider: str | None = None,
    status: str | None = None,
    cloud_account: str | None = None,
    region: str | None = None,
    keyword: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_resource:list"),
):
    """查询 CMDB 资源列表（分页）。"""
    resources, total = await resource_service.list_resources(
        session,
        model_id=model_id,
        provider=provider,
        status=status,
        cloud_account=cloud_account,
        region=region,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
    items = [
        ResourceResponse(
            id=r.id,
            model_id=r.model_id,
            name=r.name,
            provider=r.provider,
            provider_id=r.provider_id,
            cloud_account=r.cloud_account,
            region=r.region,
            zone=r.zone,
            status=r.status,
            fields=r.fields,
            resource_version=r.resource_version,
            synced_at=r.synced_at,
            source=r.source,
            created_at=r.created_at,
            updated_at=r.updated_at,
        ).model_dump(mode="json")
        for r in resources
    ]
    return paginated_response(items, total, page, page_size)


@router.get("/stats")
async def get_resource_stats(
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_resource:list"),
):
    """获取资源统计（按模型/状态/云厂商分组）。"""
    stats = await resource_service.get_resource_stats(session)
    return success_response(data=stats)


@router.post("", status_code=201)
async def create_resource(
    payload: ResourceCreate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_resource:create"),
):
    """创建 CMDB 资源实例。"""
    resource = await resource_service.create_resource(session, payload)
    data = ResourceResponse(
        id=resource.id,
        model_id=resource.model_id,
        name=resource.name,
        provider=resource.provider,
        provider_id=resource.provider_id,
        cloud_account=resource.cloud_account,
        region=resource.region,
        zone=resource.zone,
        status=resource.status,
        fields=resource.fields,
        resource_version=resource.resource_version,
        synced_at=resource.synced_at,
        source=resource.source,
        created_at=resource.created_at,
        updated_at=resource.updated_at,
    )
    return success_response(
        data=data.model_dump(mode="json"),
        message="Resource created",
        http_status=201,
    )


@router.get("/options")
async def search_resource_options(
    keyword: str | None = None,
    model_id: int | None = None,
    status: str | None = None,
    limit: int = Query(20, ge=1, le=50),
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_resource:list"),
):
    """资源选择器轻量搜索（按名称/实例 ID 模糊匹配，下拉渲染用）。"""
    options = await resource_service.search_resource_options(
        session, keyword=keyword, model_id=model_id, status=status, limit=limit,
    )
    return success_response(data=options)


@router.get("/{resource_id}")
async def get_resource(
    resource_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_resource:list"),
):
    """获取 CMDB 资源详情。"""
    resource = await resource_service.get_resource(session, resource_id)
    data = ResourceResponse(
        id=resource.id,
        model_id=resource.model_id,
        name=resource.name,
        provider=resource.provider,
        provider_id=resource.provider_id,
        cloud_account=resource.cloud_account,
        region=resource.region,
        zone=resource.zone,
        status=resource.status,
        fields=resource.fields,
        resource_version=resource.resource_version,
        synced_at=resource.synced_at,
        source=resource.source,
        created_at=resource.created_at,
        updated_at=resource.updated_at,
    )
    return success_response(data=data.model_dump(mode="json"))


@router.put("/{resource_id}")
async def update_resource(
    resource_id: int,
    payload: ResourceUpdate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_resource:update"),
):
    """更新 CMDB 资源实例。"""
    resource = await resource_service.update_resource(session, resource_id, payload)
    data = ResourceResponse(
        id=resource.id,
        model_id=resource.model_id,
        name=resource.name,
        provider=resource.provider,
        provider_id=resource.provider_id,
        cloud_account=resource.cloud_account,
        region=resource.region,
        zone=resource.zone,
        status=resource.status,
        fields=resource.fields,
        resource_version=resource.resource_version,
        synced_at=resource.synced_at,
        source=resource.source,
        created_at=resource.created_at,
        updated_at=resource.updated_at,
    )
    return success_response(data=data.model_dump(mode="json"))


@router.delete("/{resource_id}")
async def delete_resource(
    resource_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_resource:delete"),
):
    """软删除 CMDB 资源实例。"""
    await resource_service.delete_resource(session, resource_id)
    return success_response(message="Resource deleted")
