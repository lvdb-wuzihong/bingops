"""中转网关 API：网关管理 + 主机可达性视图。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.api.dependencies import get_db_session, require_permission
from bingops.core.response import paginated_response, success_response
from bingops.models.job_gateway import JobGateway
from bingops.models.user import User
from bingops.schemas.job_gateway import (
    GatewayCreate,
    GatewayResponse,
    GatewayUpdate,
)
from bingops.services import gateway_service

router = APIRouter(prefix="/api/v1/job-gateways", tags=["job-gateways"])


def _to_response(gateway: JobGateway) -> dict:
    return GatewayResponse(
        id=gateway.id,
        name=gateway.name,
        host=gateway.host,
        port=gateway.port,
        login_user=gateway.login_user,
        ssh_credential=gateway.ssh_credential,
        scope=gateway.scope or {},
        priority=gateway.priority,
        remark=gateway.remark,
        is_active=gateway.is_active,
        created_by=gateway.created_by,
        created_at=gateway.created_at,
        updated_at=gateway.updated_at,
    ).model_dump(mode="json")


@router.get("")
async def list_gateways(
    keyword: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("gateway:list"),
):
    """网关列表（按 priority 升序，选路即按此顺序命中）。"""
    gateways, total = await gateway_service.list_gateways(
        session, keyword=keyword, page=page, page_size=page_size,
    )
    items = [_to_response(g) for g in gateways]
    return paginated_response(items, total, page, page_size)


@router.get("/reachability")
async def host_reachability(
    model_code: list[str] | None = Query(None, description="默认覆盖 aliyun_ecs/gcp_compute"),
    limit: int = Query(200, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("gateway:list"),
):
    """主机可达性总览：凭据是否齐、走哪个网关、缺什么。

    注意本路由必须声明在 `/{gateway_id}` 之前，否则 "reachability" 会被当成 id 解析。
    """
    rows = await gateway_service.reachability(session, model_codes=model_code, limit=limit)
    return success_response(data=rows)


@router.post("", status_code=201)
async def create_gateway(
    payload: GatewayCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = require_permission("gateway:create"),
):
    """新建网关：scope 至少要填一个维度（不提供全局兜底语义）。"""
    gateway = await gateway_service.create_gateway(session, payload, current_user)
    return success_response(
        data=_to_response(gateway), message="Gateway created", http_status=201,
    )


@router.get("/{gateway_id}")
async def get_gateway(
    gateway_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("gateway:get"),
):
    """网关详情。"""
    gateway = await gateway_service.get_gateway(session, gateway_id)
    return success_response(data=_to_response(gateway))


@router.put("/{gateway_id}")
async def update_gateway(
    gateway_id: int,
    payload: GatewayUpdate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("gateway:update"),
):
    """更新网关（is_active=false 即停用，保留历史）。"""
    gateway = await gateway_service.update_gateway(session, gateway_id, payload)
    return success_response(data=_to_response(gateway), message="Gateway updated")


@router.delete("/{gateway_id}")
async def delete_gateway(
    gateway_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("gateway:delete"),
):
    """删除网关。"""
    await gateway_service.delete_gateway(session, gateway_id)
    return success_response(message="Gateway deleted")
