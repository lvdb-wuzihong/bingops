"""CMDB 业务应用数据访问层。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.models.cmdb.business_app import CmdbBusinessApp


class CmdbBusinessAppRepo:
    """CMDB 业务应用 Repository。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, app_id: int) -> CmdbBusinessApp | None:
        """根据 ID 查询业务应用。"""
        result = await self._session.execute(
            select(CmdbBusinessApp).where(CmdbBusinessApp.id == app_id)
        )
        return result.scalar_one_or_none()

    async def get_by_app_code(self, app_code: str) -> CmdbBusinessApp | None:
        """根据 app_code 查询业务应用。"""
        result = await self._session.execute(
            select(CmdbBusinessApp).where(CmdbBusinessApp.app_code == app_code)
        )
        return result.scalar_one_or_none()

    async def list_apps(
        self,
        *,
        team: str | None = None,
        owner: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[CmdbBusinessApp], int]:
        """分页查询业务应用列表。"""
        query = select(CmdbBusinessApp)
        count_query = select(CmdbBusinessApp.id)

        if team:
            query = query.where(CmdbBusinessApp.team == team)
            count_query = count_query.where(CmdbBusinessApp.team == team)
        if owner:
            query = query.where(CmdbBusinessApp.owner == owner)
            count_query = count_query.where(CmdbBusinessApp.owner == owner)
        if keyword:
            like_pattern = f"%{keyword}%"
            query = query.where(CmdbBusinessApp.name.ilike(like_pattern))
            count_query = count_query.where(CmdbBusinessApp.name.ilike(like_pattern))

        total_result = await self._session.execute(
            select(func.count()).select_from(count_query.subquery())
        )
        total = total_result.scalar() or 0

        query = query.order_by(CmdbBusinessApp.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)
        result = await self._session.execute(query)
        return list(result.scalars().all()), total

    async def create(self, app: CmdbBusinessApp) -> CmdbBusinessApp:
        """创建业务应用。"""
        self._session.add(app)
        await self._session.flush()
        return app

    async def update(self, app: CmdbBusinessApp) -> CmdbBusinessApp:
        """更新业务应用。"""
        await self._session.flush()
        return app

    async def delete(self, app: CmdbBusinessApp) -> None:
        """删除业务应用。"""
        await self._session.delete(app)
        await self._session.flush()
