"""CMDB 变更审计 API 路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.api.dependencies import get_db_session, require_permission
from bingops.core.response import paginated_response
from bingops.models.user import User
from bingops.schemas.cmdb.change_log import ChangeLogResponse
from bingops.services.cmdb import change_log_service

router = APIRouter(prefix="/api/v1/cmdb/changes", tags=["cmdb-changes"])


@router.get("")
async def list_changes(
    resource_id: int | None = None,
    change_type: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_change:list"),
):
    """分页查询变更记录。"""
    logs, total = await change_log_service.list_changes(
        session, resource_id=resource_id, change_type=change_type, page=page, page_size=page_size,
    )
    items = [
        ChangeLogResponse(
            id=log.id,
            resource_id=log.resource_id,
            resource_type=log.resource_type,
            change_type=log.change_type,
            field=log.field,
            old_value=log.old_value,
            new_value=log.new_value,
            source=log.source,
            operator=log.operator,
            created_at=log.created_at,
        ).model_dump(mode="json")
        for log in logs
    ]
    return paginated_response(items, total, page, page_size)
