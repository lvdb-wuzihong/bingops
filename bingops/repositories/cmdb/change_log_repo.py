"""CMDB 变更记录数据访问层。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.models.cmdb.change_log import CmdbChangeLog


class CmdbChangeLogRepo:
    """CMDB 变更记录 Repository（只追加，不可修改/删除）。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, log: CmdbChangeLog) -> CmdbChangeLog:
        """写入一条变更记录。"""
        self._session.add(log)
        await self._session.flush()
        return log

    async def list_logs(
        self,
        *,
        resource_id: int | None = None,
        change_type: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[CmdbChangeLog], int]:
        """分页查询变更记录。"""
        query = select(CmdbChangeLog)
        count_query = select(CmdbChangeLog.id)

        if resource_id is not None:
            query = query.where(CmdbChangeLog.resource_id == resource_id)
            count_query = count_query.where(CmdbChangeLog.resource_id == resource_id)
        if change_type:
            query = query.where(CmdbChangeLog.change_type == change_type)
            count_query = count_query.where(CmdbChangeLog.change_type == change_type)

        total_result = await self._session.execute(
            select(func.count()).select_from(count_query.subquery())
        )
        total = total_result.scalar() or 0

        query = query.order_by(CmdbChangeLog.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)
        result = await self._session.execute(query)
        return list(result.scalars().all()), total
