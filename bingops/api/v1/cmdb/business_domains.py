"""CMDB 业务域 API 路由（业务域 → 应用 → 资源 三层归属的业务分组层）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.api.dependencies import get_db_session, require_permission
from bingops.core.exceptions import NotFoundError
from bingops.core.response import success_response
from bingops.models.user import User
from bingops.repositories.cmdb.business_app_repo import CmdbBusinessDomainRepo
from bingops.schemas.cmdb.business_app import BusinessDomainCreate, BusinessDomainUpdate
from bingops.services.cmdb import business_app_service

router = APIRouter(prefix="/api/v1/cmdb/business-domains", tags=["cmdb-business-domains"])


@router.get("")
async def list_domains(
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_app:list"),
):
    """业务域列表（含归属应用数）。"""
    items = await business_app_service.list_domains(session)
    return success_response(data=items)


@router.post("", status_code=201)
async def create_domain(
    payload: BusinessDomainCreate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_app:create"),
):
    """创建业务域。"""
    domain = await business_app_service.create_domain(session, payload)
    return success_response(
        data={"id": domain.id, "code": domain.code, "name": domain.name},
    )


@router.put("/{domain_id}")
async def update_domain(
    domain_id: int,
    payload: BusinessDomainUpdate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_app:update"),
):
    """更新业务域。"""
    domain = await business_app_service.update_domain(session, domain_id, payload)
    return success_response(
        data={"id": domain.id, "code": domain.code, "name": domain.name},
    )


@router.get("/{domain_id}/apps")
async def list_domain_apps(
    domain_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_app:list"),
):
    """归属该业务域的应用清单。"""
    repo = CmdbBusinessDomainRepo(session)
    domain = await repo.get_by_id(domain_id)
    if domain is None:
        raise NotFoundError("CmdbBusinessDomain", str(domain_id))
    apps, total = await business_app_service.list_apps(session, page=1, page_size=500)
    items = [
        {"id": a.id, "app_code": a.app_code, "name": a.name,
         "owner": a.owner, "team": a.team}
        for a in apps if a.business_id == domain_id
    ]
    return success_response(data={"domain": {"id": domain.id, "name": domain.name}, "items": items, "total": total})
