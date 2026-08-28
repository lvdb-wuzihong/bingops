"""CMDB 业务应用 API 路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.api.dependencies import get_db_session, require_permission
from bingops.core.response import paginated_response, success_response
from bingops.models.user import User
from bingops.schemas.cmdb.business_app import BusinessAppCreate, BusinessAppResponse, BusinessAppUpdate
from bingops.services.cmdb import business_app_service

router = APIRouter(prefix="/api/v1/cmdb/apps", tags=["cmdb-apps"])


@router.get("")
async def list_apps(
    team: str | None = None,
    owner: str | None = None,
    keyword: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_app:list"),
):
    """分页查询业务应用列表。"""
    apps, total = await business_app_service.list_apps(
        session, team=team, owner=owner, keyword=keyword, page=page, page_size=page_size,
    )
    items = [
        BusinessAppResponse(
            id=a.id,
            app_code=a.app_code,
            name=a.name,
            description=a.description,
            team=a.team,
            owner=a.owner,
            department=a.department,
            labels=a.labels,
            repo_url=a.repo_url,
            pipelines=a.pipelines,
            created_at=a.created_at,
            updated_at=a.updated_at,
        ).model_dump(mode="json")
        for a in apps
    ]
    return paginated_response(items, total, page, page_size)


@router.post("", status_code=201)
async def create_app(
    payload: BusinessAppCreate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_app:create"),
):
    """创建业务应用。"""
    app = await business_app_service.create_app(session, payload)
    data = BusinessAppResponse(
        id=app.id,
        app_code=app.app_code,
        name=app.name,
        description=app.description,
        team=app.team,
        owner=app.owner,
        department=app.department,
        labels=app.labels,
        repo_url=app.repo_url,
        pipelines=app.pipelines,
        created_at=app.created_at,
        updated_at=app.updated_at,
    )
    return success_response(data=data.model_dump(mode="json"), message="Business app created", http_status=201)


@router.get("/{app_id}")
async def get_app(
    app_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_app:list"),
):
    """获取业务应用详情。"""
    app = await business_app_service.get_app(session, app_id)
    data = BusinessAppResponse(
        id=app.id,
        app_code=app.app_code,
        name=app.name,
        description=app.description,
        team=app.team,
        owner=app.owner,
        department=app.department,
        labels=app.labels,
        repo_url=app.repo_url,
        pipelines=app.pipelines,
        created_at=app.created_at,
        updated_at=app.updated_at,
    )
    return success_response(data=data.model_dump(mode="json"))


@router.put("/{app_id}")
async def update_app(
    app_id: int,
    payload: BusinessAppUpdate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_app:update"),
):
    """更新业务应用。"""
    app = await business_app_service.update_app(session, app_id, payload)
    data = BusinessAppResponse(
        id=app.id,
        app_code=app.app_code,
        name=app.name,
        description=app.description,
        team=app.team,
        owner=app.owner,
        department=app.department,
        labels=app.labels,
        repo_url=app.repo_url,
        pipelines=app.pipelines,
        created_at=app.created_at,
        updated_at=app.updated_at,
    )
    return success_response(data=data.model_dump(mode="json"))


@router.delete("/{app_id}")
async def delete_app(
    app_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_app:delete"),
):
    """删除业务应用。"""
    await business_app_service.delete_app(session, app_id)
    return success_response(message="Business app deleted")


# ── 应用-资源关联（#13 物化）──────────────────────────────────


class ResourceBindRequest(BaseModel):
    resource_id: int


@router.get("/{app_id}/resources")
async def list_app_resources(
    app_id: int,
    env: str | None = Query(default=None, description="按环境标签过滤（env/k8s:env）"),
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_app:list"),
):
    """应用下的资源列表。每项含 env/region，前端可按环境分组展示。"""
    items = await business_app_service.list_app_resources(session, app_id, env)
    return success_response(data=items)


@router.post("/{app_id}/resources", status_code=201)
async def bind_resource(
    app_id: int,
    payload: ResourceBindRequest,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_app:update"),
):
    """手动绑定应用与资源（仅服务级 CI）。"""
    await business_app_service.bind_resource(session, app_id, payload.resource_id)
    return success_response(message="Resource bound to app", http_status=201)


@router.delete("/{app_id}/resources/{resource_id}")
async def unbind_resource(
    app_id: int,
    resource_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_app:update"),
):
    """解绑应用与资源。"""
    await business_app_service.unbind_resource(session, app_id, resource_id)
    return success_response(message="Resource unbound from app")


@router.get("/by-resource/{resource_id}")
async def list_resource_apps(
    resource_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_app:list"),
):
    """资源归属的应用列表。"""
    items = await business_app_service.list_resource_apps(session, resource_id)
    return success_response(data=items)
