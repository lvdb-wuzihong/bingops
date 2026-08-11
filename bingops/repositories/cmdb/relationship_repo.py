"""CMDB 关系数据访问层（从属关系 + 关联关系）。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.models.cmdb.relationship import CmdbBelongsTo, CmdbRelatesTo


class CmdbRelationshipRepo:
    """CMDB 关系 Repository，封装从属和关联关系的数据库操作。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── 从属关系 (Belongs To) ────────────────────────────────────────────────────

    async def create_belongs_to(self, relation: CmdbBelongsTo) -> CmdbBelongsTo:
        """创建从属关系。"""
        self._session.add(relation)
        await self._session.flush()
        return relation

    async def delete_belongs_to(self, relation_id: int) -> CmdbBelongsTo | None:
        """删除从属关系。"""
        result = await self._session.execute(
            select(CmdbBelongsTo).where(CmdbBelongsTo.id == relation_id)
        )
        relation = result.scalar_one_or_none()
        if relation is None:
            return None
        await self._session.delete(relation)
        await self._session.flush()
        return relation

    async def get_belongs_to_by_id(self, relation_id: int) -> CmdbBelongsTo | None:
        """根据 ID 查询从属关系。"""
        result = await self._session.execute(
            select(CmdbBelongsTo).where(CmdbBelongsTo.id == relation_id)
        )
        return result.scalar_one_or_none()

    async def get_children(self, parent_id: int, relation_type: str | None = None) -> list[CmdbBelongsTo]:
        """查询某资源的所有子资源关系。"""
        query = select(CmdbBelongsTo).where(CmdbBelongsTo.parent_id == parent_id)
        if relation_type:
            query = query.where(CmdbBelongsTo.relation_type == relation_type)
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def get_parents(self, child_id: int, relation_type: str | None = None) -> list[CmdbBelongsTo]:
        """查询某资源的所有父资源关系。"""
        query = select(CmdbBelongsTo).where(CmdbBelongsTo.child_id == child_id)
        if relation_type:
            query = query.where(CmdbBelongsTo.relation_type == relation_type)
        result = await self._session.execute(query)
        return list(result.scalars().all())

    # ── 关联关系 (Relates To) ────────────────────────────────────────────────────

    async def create_relates_to(self, relation: CmdbRelatesTo) -> CmdbRelatesTo:
        """创建关联关系。"""
        self._session.add(relation)
        await self._session.flush()
        return relation

    async def delete_relates_to(self, relation_id: int) -> CmdbRelatesTo | None:
        """删除关联关系。"""
        result = await self._session.execute(
            select(CmdbRelatesTo).where(CmdbRelatesTo.id == relation_id)
        )
        relation = result.scalar_one_or_none()
        if relation is None:
            return None
        await self._session.delete(relation)
        await self._session.flush()
        return relation

    async def get_relates_to_by_id(self, relation_id: int) -> CmdbRelatesTo | None:
        """根据 ID 查询关联关系。"""
        result = await self._session.execute(
            select(CmdbRelatesTo).where(CmdbRelatesTo.id == relation_id)
        )
        return result.scalar_one_or_none()

    async def get_relations_from(self, source_id: int, relation_type: str | None = None) -> list[CmdbRelatesTo]:
        """查询从某资源出发的所有关联关系。"""
        query = select(CmdbRelatesTo).where(CmdbRelatesTo.source_id == source_id)
        if relation_type:
            query = query.where(CmdbRelatesTo.relation_type == relation_type)
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def get_relations_to(self, target_id: int, relation_type: str | None = None) -> list[CmdbRelatesTo]:
        """查询指向某资源的所有关联关系。"""
        query = select(CmdbRelatesTo).where(CmdbRelatesTo.target_id == target_id)
        if relation_type:
            query = query.where(CmdbRelatesTo.relation_type == relation_type)
        result = await self._session.execute(query)
        return list(result.scalars().all())
