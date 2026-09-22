"""CMDB 资源收藏 Repository（我的关注）。"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.models.cmdb.favorite import CmdbResourceFavorite


class CmdbFavoriteRepo:
    """资源收藏数据访问。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_user(self, user_id: int) -> list[CmdbResourceFavorite]:
        """按收藏时间倒序返回用户的全部收藏。"""
        result = await self._session.execute(
            select(CmdbResourceFavorite)
            .where(CmdbResourceFavorite.user_id == user_id)
            .order_by(CmdbResourceFavorite.created_at.desc())
        )
        return list(result.scalars().all())

    async def get(self, user_id: int, resource_id: int) -> CmdbResourceFavorite | None:
        result = await self._session.execute(
            select(CmdbResourceFavorite).where(
                CmdbResourceFavorite.user_id == user_id,
                CmdbResourceFavorite.resource_id == resource_id,
            )
        )
        return result.scalar_one_or_none()

    async def add(self, user_id: int, resource_id: int) -> CmdbResourceFavorite | None:
        """收藏（幂等：已存在返回 None 表示无变化）。"""
        if await self.get(user_id, resource_id) is not None:
            return None
        fav = CmdbResourceFavorite(user_id=user_id, resource_id=resource_id)
        self._session.add(fav)
        await self._session.flush()
        return fav

    async def remove(self, user_id: int, resource_id: int) -> bool:
        """取消收藏，返回是否真的删除。"""
        result = await self._session.execute(
            delete(CmdbResourceFavorite).where(
                CmdbResourceFavorite.user_id == user_id,
                CmdbResourceFavorite.resource_id == resource_id,
            )
        )
        return (result.rowcount or 0) > 0
