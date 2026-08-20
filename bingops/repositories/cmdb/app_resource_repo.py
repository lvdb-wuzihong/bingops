"""CMDB 应用-资源关联 Repository。"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.models.cmdb.app_resource import CmdbAppResource


class CmdbAppResourceRepo:
    """应用-资源关联表数据访问。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_app(self, app_id: int) -> list[CmdbAppResource]:
        result = await self._session.execute(
            select(CmdbAppResource).where(CmdbAppResource.app_id == app_id)
        )
        return list(result.scalars().all())

    async def list_by_resource(self, resource_id: int) -> list[CmdbAppResource]:
        result = await self._session.execute(
            select(CmdbAppResource).where(CmdbAppResource.resource_id == resource_id)
        )
        return list(result.scalars().all())

    async def list_tag_links(self, resource_id: int) -> list[CmdbAppResource]:
        """source='tag' 的自动归集关联（替换式管理用）。"""
        result = await self._session.execute(
            select(CmdbAppResource).where(
                CmdbAppResource.resource_id == resource_id,
                CmdbAppResource.source == "tag",
            )
        )
        return list(result.scalars().all())

    async def replace_tag_links(self, resource_id: int, app_ids: set[int]) -> None:
        """差集替换 source='tag' 关联；manual 关联不受影响。"""
        current = await self.list_tag_links(resource_id)
        current_ids = {link.app_id for link in current}
        for link in current:
            if link.app_id not in app_ids:
                await self._session.delete(link)
        for app_id in app_ids - current_ids:
            self._session.add(CmdbAppResource(
                app_id=app_id, resource_id=resource_id, source="tag",
            ))
        await self._session.flush()

    async def get_link(self, app_id: int, resource_id: int) -> CmdbAppResource | None:
        result = await self._session.execute(
            select(CmdbAppResource).where(
                CmdbAppResource.app_id == app_id,
                CmdbAppResource.resource_id == resource_id,
            )
        )
        return result.scalar_one_or_none()

    async def add_manual(self, app_id: int, resource_id: int) -> CmdbAppResource:
        link = CmdbAppResource(app_id=app_id, resource_id=resource_id, source="manual")
        self._session.add(link)
        await self._session.flush()
        return link

    async def remove_link(self, link: CmdbAppResource) -> None:
        await self._session.delete(link)
        await self._session.flush()

    async def delete_by_app(self, app_id: int) -> None:
        await self._session.execute(
            delete(CmdbAppResource).where(CmdbAppResource.app_id == app_id)
        )
