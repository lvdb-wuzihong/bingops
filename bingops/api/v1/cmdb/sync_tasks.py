"""CMDB 同步任务管理 API 路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.api.dependencies import get_db_session, require_permission
from bingops.core.response import paginated_response, success_response
from bingops.models.user import User
from bingops.schemas.cmdb.sync_task import SyncTaskCreate, SyncTaskResponse, SyncTaskUpdate
from bingops.services.cmdb import sync_task_service

router = APIRouter(prefix="/api/v1/cmdb/sync-tasks", tags=["cmdb-sync-tasks"])


def _to_response(task) -> dict:
    """ORM 模型转响应字典。"""
    return SyncTaskResponse(
        id=task.id,
        name=task.name,
        task_type=task.task_type,
        provider=task.provider,
        target_id=task.target_id,
        resource_types=task.resource_types or [],
        schedule=task.schedule,
        enabled=task.enabled,
        description=task.description,
        last_synced_at=task.last_synced_at,
        created_at=task.created_at,
        updated_at=task.updated_at,
    ).model_dump(mode="json")


@router.get("")
async def list_sync_tasks(
    task_type: str | None = None,
    enabled: bool | None = None,
    keyword: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_sync_task:list"),
):
    """查询同步任务列表（分页）。"""
    tasks, total = await sync_task_service.list_sync_tasks(
        session,
        task_type=task_type,
        enabled=enabled,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
    items = [_to_response(t) for t in tasks]
    return paginated_response(items, total, page, page_size)


@router.post("", status_code=201)
async def create_sync_task(
    payload: SyncTaskCreate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_sync_task:create"),
):
    """创建同步任务。"""
    task = await sync_task_service.create_sync_task(session, payload)
    return success_response(data=_to_response(task), message="Sync task created", http_status=201)


@router.get("/{task_id}")
async def get_sync_task(
    task_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_sync_task:list"),
):
    """获取同步任务详情。"""
    task = await sync_task_service.get_sync_task(session, task_id)
    return success_response(data=_to_response(task))


@router.put("/{task_id}")
async def update_sync_task(
    task_id: int,
    payload: SyncTaskUpdate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_sync_task:update"),
):
    """更新同步任务。"""
    task = await sync_task_service.update_sync_task(session, task_id, payload)
    return success_response(data=_to_response(task))


@router.delete("/{task_id}")
async def delete_sync_task(
    task_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_sync_task:delete"),
):
    """删除同步任务。"""
    await sync_task_service.delete_sync_task(session, task_id)
    return success_response(message="Sync task deleted")


class ToggleRequest(BaseModel):
    """启停请求体。"""

    enabled: bool = Field(description="是否启用")


@router.patch("/{task_id}/toggle")
async def toggle_sync_task(
    task_id: int,
    payload: ToggleRequest,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_sync_task:update"),
):
    """启用/禁用同步任务。"""
    task = await sync_task_service.toggle_sync_task(session, task_id, payload.enabled)
    return success_response(data=_to_response(task), message=f"Sync task {'enabled' if payload.enabled else 'disabled'}")
