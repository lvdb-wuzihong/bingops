"""CMDB 变更审计 API 路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.api.dependencies import get_db_session, require_permission
from bingops.core.response import paginated_response
from bingops.models.cmdb.model import CmdbModel
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

    # v2 审计表以 model_id 关联模型定义，解析出 code/name 填充响应
    model_ids = {log.model_id for log in logs if log.model_id is not None}
    models: dict[int, CmdbModel] = {}
    if model_ids:
        rows = await session.execute(select(CmdbModel).where(CmdbModel.id.in_(model_ids)))
        models = {m.id: m for m in rows.scalars().all()}

    items = []
    for log in logs:
        model = models.get(log.model_id) if log.model_id is not None else None
        items.append(
            ChangeLogResponse(
                id=log.id,
                resource_id=log.resource_id,
                model_id=log.model_id,
                model_code=model.code if model else None,
                resource_type=model.name if model else "-",
                change_type=log.change_type,
                field=log.field,
                old_value=log.old_value,
                new_value=log.new_value,
                source=log.source,
                operator=log.operator,
                created_at=log.created_at,
            ).model_dump(mode="json")
        )
    return paginated_response(items, total, page, page_size)
