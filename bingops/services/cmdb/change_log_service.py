"""CMDB 变更审计服务。"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from bingops.models.cmdb.change_log import CmdbChangeLog
from bingops.repositories.cmdb.change_log_repo import CmdbChangeLogRepo
from bingops.schemas.cmdb.change_log import ChangeLogCreate

logger = logging.getLogger(f"bingops.{__name__}")


async def record_change(session: AsyncSession, payload: ChangeLogCreate) -> CmdbChangeLog:
    """记录一条变更日志（内部调用，不暴露为 API）。"""
    repo = CmdbChangeLogRepo(session)
    log = CmdbChangeLog(
        resource_id=payload.resource_id,
        resource_type=payload.resource_type,
        change_type=payload.change_type,
        field=payload.field,
        old_value=payload.old_value,
        new_value=payload.new_value,
        source=payload.source,
        operator=payload.operator,
    )
    log = await repo.create(log)
    await session.commit()
    return log


async def list_changes(
    session: AsyncSession,
    *,
    resource_id: int | None = None,
    change_type: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[CmdbChangeLog], int]:
    """分页查询变更记录。"""
    repo = CmdbChangeLogRepo(session)
    return await repo.list_logs(
        resource_id=resource_id,
        change_type=change_type,
        page=page,
        page_size=page_size,
    )
