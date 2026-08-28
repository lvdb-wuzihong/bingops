"""服务目录/处理组/值班表 API 路由。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.api.dependencies import get_db_session, require_permission
from bingops.core.response import success_response
from bingops.models.ticket import OncallSchedule, TicketCatalog, TicketGroup
from bingops.models.user import User
from bingops.schemas.ticket import (
    CatalogCreate,
    CatalogResponse,
    CatalogUpdate,
    GroupCreate,
    GroupResponse,
    GroupUpdate,
    OncallCreate,
    OncallResponse,
    OncallUpdate,
)
from bingops.services import ticket_meta_service

catalog_router = APIRouter(prefix="/api/v1/ticket-catalog", tags=["ticket-catalog"])
group_router = APIRouter(prefix="/api/v1/ticket-groups", tags=["ticket-groups"])
oncall_router = APIRouter(prefix="/api/v1/oncall-schedules", tags=["oncall-schedules"])


def _catalog_to_response(item: TicketCatalog) -> dict:
    """ORM 目录项转响应字典。"""
    return CatalogResponse(
        id=item.id,
        name=item.name,
        parent_id=item.parent_id,
        description=item.description,
        difficulty=item.difficulty,
        default_risk=item.default_risk,
        default_type=item.default_type,
        default_runbook_id=item.default_runbook_id,
        is_active=item.is_active,
        sort_order=item.sort_order,
        created_at=item.created_at,
        updated_at=item.updated_at,
    ).model_dump(mode="json")


def _group_to_response(group: TicketGroup) -> dict:
    """ORM 处理组转响应字典。"""
    return GroupResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        members=group.members,
        is_active=group.is_active,
        created_at=group.created_at,
        updated_at=group.updated_at,
    ).model_dump(mode="json")


def _oncall_to_response(schedule: OncallSchedule) -> dict:
    """ORM 值班排班转响应字典。"""
    return OncallResponse(
        id=schedule.id,
        group_id=schedule.group_id,
        group_name=schedule.group.name if schedule.group else None,
        oncall_date=schedule.oncall_date,
        tier1=schedule.tier1,
        tier2=schedule.tier2,
        tier3=schedule.tier3,
        note=schedule.note,
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
    ).model_dump(mode="json")


# ── 服务目录 ──────────────────────────────────────────────────────────────────


@catalog_router.get("")
async def list_catalog(
    parent_id: int | None = None,
    include_inactive: bool = False,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("ticket_catalog:list"),
):
    """服务目录列表（两级，前端按 parent_id 组树）。"""
    items = await ticket_meta_service.list_catalog(
        session, parent_id=parent_id, include_inactive=include_inactive,
    )
    return success_response(data=[_catalog_to_response(i) for i in items])


@catalog_router.post("", status_code=201)
async def create_catalog_item(
    payload: CatalogCreate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("ticket_catalog:create"),
):
    """创建目录项（最多两级）。"""
    item = await ticket_meta_service.create_catalog_item(session, payload)
    return success_response(
        data=_catalog_to_response(item), message="Catalog item created", http_status=201,
    )


@catalog_router.put("/{item_id}")
async def update_catalog_item(
    item_id: int,
    payload: CatalogUpdate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("ticket_catalog:update"),
):
    """更新目录项。"""
    item = await ticket_meta_service.update_catalog_item(session, item_id, payload)
    return success_response(data=_catalog_to_response(item))


@catalog_router.delete("/{item_id}")
async def delete_catalog_item(
    item_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("ticket_catalog:delete"),
):
    """删除目录项（有子项或被工单引用时拒绝）。"""
    await ticket_meta_service.delete_catalog_item(session, item_id)
    return success_response(message="Catalog item deleted")


# ── 处理组 ────────────────────────────────────────────────────────────────────


@group_router.get("")
async def list_groups(
    include_inactive: bool = False,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("ticket_group:list"),
):
    """处理组列表。"""
    groups = await ticket_meta_service.list_groups(session, include_inactive=include_inactive)
    return success_response(data=[_group_to_response(g) for g in groups])


@group_router.post("", status_code=201)
async def create_group(
    payload: GroupCreate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("ticket_group:create"),
):
    """创建处理组。"""
    group = await ticket_meta_service.create_group(session, payload)
    return success_response(
        data=_group_to_response(group), message="Ticket group created", http_status=201,
    )


@group_router.put("/{group_id}")
async def update_group(
    group_id: int,
    payload: GroupUpdate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("ticket_group:update"),
):
    """更新处理组。"""
    group = await ticket_meta_service.update_group(session, group_id, payload)
    return success_response(data=_group_to_response(group))


@group_router.delete("/{group_id}")
async def delete_group(
    group_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("ticket_group:delete"),
):
    """删除处理组（被工单引用时拒绝）。"""
    await ticket_meta_service.delete_group(session, group_id)
    return success_response(message="Ticket group deleted")


# ── 值班表 ────────────────────────────────────────────────────────────────────


@oncall_router.get("")
async def list_oncall(
    group_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("oncall:list"),
):
    """值班排班列表（按组/日期范围过滤）。"""
    schedules = await ticket_meta_service.list_oncall(
        session, group_id=group_id, date_from=date_from, date_to=date_to,
    )
    return success_response(data=[_oncall_to_response(s) for s in schedules])


@oncall_router.post("", status_code=201)
async def create_oncall(
    payload: OncallCreate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("oncall:create"),
):
    """创建值班排班（同组同日期唯一）。"""
    schedule = await ticket_meta_service.create_oncall(session, payload)
    return success_response(
        data=_oncall_to_response(schedule), message="Oncall schedule created", http_status=201,
    )


@oncall_router.put("/{schedule_id}")
async def update_oncall(
    schedule_id: int,
    payload: OncallUpdate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("oncall:update"),
):
    """更新值班排班。"""
    schedule = await ticket_meta_service.update_oncall(session, schedule_id, payload)
    return success_response(data=_oncall_to_response(schedule))


@oncall_router.delete("/{schedule_id}")
async def delete_oncall(
    schedule_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("oncall:delete"),
):
    """删除值班排班。"""
    await ticket_meta_service.delete_oncall(session, schedule_id)
    return success_response(message="Oncall schedule deleted")
