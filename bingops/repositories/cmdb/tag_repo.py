"""CMDB 标签数据访问层。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.models.cmdb.tag import CmdbResourceTag, CmdbTagDefinition


class CmdbTagRepo:
    """CMDB 标签 Repository，封装标签定义和资源标签的数据库操作。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── 标签定义 (Tag Definition) ────────────────────────────────────────────────

    async def create_tag_definition(self, tag_def: CmdbTagDefinition) -> CmdbTagDefinition:
        """创建标签定义。"""
        self._session.add(tag_def)
        await self._session.flush()
        return tag_def

    async def get_tag_definition_by_id(self, tag_def_id: int) -> CmdbTagDefinition | None:
        """根据 ID 查询标签定义。"""
        result = await self._session.execute(
            select(CmdbTagDefinition).where(CmdbTagDefinition.id == tag_def_id)
        )
        return result.scalar_one_or_none()

    async def get_tag_definition_by_key(self, tag_key: str) -> CmdbTagDefinition | None:
        """根据 tag_key 查询标签定义。"""
        result = await self._session.execute(
            select(CmdbTagDefinition).where(CmdbTagDefinition.tag_key == tag_key)
        )
        return result.scalar_one_or_none()

    async def list_tag_definitions(
        self,
        *,
        category: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[CmdbTagDefinition], int]:
        """分页查询标签定义列表。"""
        query = select(CmdbTagDefinition)
        count_query = select(CmdbTagDefinition.id)

        if category:
            query = query.where(CmdbTagDefinition.category == category)
            count_query = count_query.where(CmdbTagDefinition.category == category)

        total_result = await self._session.execute(
            select(func.count()).select_from(count_query.subquery())
        )
        total = total_result.scalar() or 0

        query = query.order_by(CmdbTagDefinition.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)
        result = await self._session.execute(query)
        return list(result.scalars().all()), total

    async def update_tag_definition(self, tag_def: CmdbTagDefinition) -> CmdbTagDefinition:
        """更新标签定义。"""
        await self._session.flush()
        return tag_def

    async def delete_tag_definition(self, tag_def: CmdbTagDefinition) -> None:
        """删除标签定义。"""
        await self._session.delete(tag_def)
        await self._session.flush()

    # ── 资源标签 (Resource Tag) ──────────────────────────────────────────────────

    async def add_resource_tag(self, tag: CmdbResourceTag) -> CmdbResourceTag:
        """为资源添加标签（upsert 语义：同 key+source 存在则更新值）。"""
        self._session.add(tag)
        await self._session.flush()
        return tag

    async def remove_resource_tag(
        self, resource_id: int, tag_key: str, source: str | None = None,
    ) -> int:
        """删除资源的指定标签，返回删除条数。"""
        query = select(CmdbResourceTag).where(
            CmdbResourceTag.resource_id == resource_id,
            CmdbResourceTag.tag_key == tag_key,
        )
        if source:
            query = query.where(CmdbResourceTag.source == source)

        result = await self._session.execute(query)
        tags = list(result.scalars().all())
        for tag in tags:
            await self._session.delete(tag)
        await self._session.flush()
        return len(tags)

    async def get_resource_tags(self, resource_id: int) -> list[CmdbResourceTag]:
        """获取某资源的所有标签。"""
        result = await self._session.execute(
            select(CmdbResourceTag)
            .where(CmdbResourceTag.resource_id == resource_id)
            .order_by(CmdbResourceTag.tag_key)
        )
        return list(result.scalars().all())

    async def find_resources_by_tag(
        self, tag_key: str, tag_value: str | None = None,
    ) -> list[int]:
        """按标签查询资源 ID 列表。"""
        query = select(CmdbResourceTag.resource_id).where(
            CmdbResourceTag.tag_key == tag_key,
        )
        if tag_value is not None:
            query = query.where(CmdbResourceTag.tag_value == tag_value)

        result = await self._session.execute(query)
        return [row[0] for row in result.all()]

    async def batch_add_tags(self, tags: list[CmdbResourceTag]) -> list[CmdbResourceTag]:
        """批量添加标签。"""
        self._session.add_all(tags)
        await self._session.flush()
        return tags
