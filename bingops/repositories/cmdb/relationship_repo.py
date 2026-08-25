"""CMDB 关系数据访问层（从属关系 + 关联关系）。

v2 表结构：关系语义通过 description 表达，无 relation_type 列。
"""

from __future__ import annotations

from sqlalchemy import or_, select
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

    async def get_children(self, parent_id: int, description: str | None = None) -> list[CmdbBelongsTo]:
        """查询某资源的所有子资源关系。"""
        query = select(CmdbBelongsTo).where(CmdbBelongsTo.parent_id == parent_id)
        if description:
            query = query.where(CmdbBelongsTo.description == description)
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def get_parents(self, child_id: int, description: str | None = None) -> list[CmdbBelongsTo]:
        """查询某资源的所有父资源关系。"""
        query = select(CmdbBelongsTo).where(CmdbBelongsTo.child_id == child_id)
        if description:
            query = query.where(CmdbBelongsTo.description == description)
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

    async def get_relations_from(self, source_id: int, description: str | None = None) -> list[CmdbRelatesTo]:
        """查询从某资源出发的所有关联关系。"""
        query = select(CmdbRelatesTo).where(CmdbRelatesTo.source_id == source_id)
        if description:
            query = query.where(CmdbRelatesTo.description == description)
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def get_relations_to(self, target_id: int, description: str | None = None) -> list[CmdbRelatesTo]:
        """查询指向某资源的所有关联关系。"""
        query = select(CmdbRelatesTo).where(CmdbRelatesTo.target_id == target_id)
        if description:
            query = query.where(CmdbRelatesTo.description == description)
        result = await self._session.execute(query)
        return list(result.scalars().all())

    # ── 批量操作（消费端关系重建用） ──────────────────────────────────────

    async def delete_belongs_to_by_child(self, child_id: int) -> int:
        """删除某资源作为子节点的全部从属关系，返回删除条数。"""
        result = await self._session.execute(
            select(CmdbBelongsTo).where(CmdbBelongsTo.child_id == child_id)
        )
        relations = list(result.scalars().all())
        for relation in relations:
            await self._session.delete(relation)
        if relations:
            await self._session.flush()
        return len(relations)

    async def delete_relates_to_by_source(self, source_id: int) -> int:
        """删除某资源作为源节点的全部关联关系，返回删除条数。"""
        result = await self._session.execute(
            select(CmdbRelatesTo).where(CmdbRelatesTo.source_id == source_id)
        )
        relations = list(result.scalars().all())
        for relation in relations:
            await self._session.delete(relation)
        if relations:
            await self._session.flush()
        return len(relations)

    async def delete_relates_to_by_source_kind(self, source_id: int, kind: str) -> int:
        """删除某资源作为源节点、指定 kind 的关联关系（槽位级替换用）。"""
        result = await self._session.execute(
            select(CmdbRelatesTo).where(
                CmdbRelatesTo.source_id == source_id,
                CmdbRelatesTo.kind == kind,
            )
        )
        relations = list(result.scalars().all())
        for relation in relations:
            await self._session.delete(relation)
        await self._session.flush()
        return len(relations)

    async def delete_relates_to_by_source_description(
        self, source_id: int, description: str,
    ) -> int:
        """按源节点+描述删除关联边（kind 无关，兼容存量 kind='' 边）。"""
        result = await self._session.execute(
            select(CmdbRelatesTo).where(
                CmdbRelatesTo.source_id == source_id,
                CmdbRelatesTo.description == description,
            )
        )
        relations = list(result.scalars().all())
        for relation in relations:
            await self._session.delete(relation)
        if relations:
            await self._session.flush()
        return len(relations)

    async def delete_relations_of(self, resource_id: int) -> int:
        """删除资源相关的全部边（两个方向、两种表），软删除时清理用。"""
        total = await self.delete_belongs_to_by_child(resource_id)
        result = await self._session.execute(
            select(CmdbBelongsTo).where(CmdbBelongsTo.parent_id == resource_id)
        )
        for relation in result.scalars().all():
            await self._session.delete(relation)
            total += 1
        total += await self.delete_relates_to_by_source(resource_id)
        result = await self._session.execute(
            select(CmdbRelatesTo).where(CmdbRelatesTo.target_id == resource_id)
        )
        for relation in result.scalars().all():
            await self._session.delete(relation)
            total += 1
        if total:
            await self._session.flush()
        return total

    # ── 拓扑子图批量查询 ───────────────────────────────────────────────────────

    async def list_belongs_to_involving(
        self, resource_ids: list[int],
    ) -> list[CmdbBelongsTo]:
        """批量查任一端点在给定集合内的 belongs_to 边（拓扑 BFS 展开用）。"""
        if not resource_ids:
            return []
        result = await self._session.execute(
            select(CmdbBelongsTo).where(
                or_(
                    CmdbBelongsTo.child_id.in_(resource_ids),
                    CmdbBelongsTo.parent_id.in_(resource_ids),
                )
            )
        )
        return list(result.scalars().all())

    async def list_relates_to_involving(
        self, resource_ids: list[int],
    ) -> list[CmdbRelatesTo]:
        """批量查任一端点在给定集合内的 relates_to 边（拓扑 BFS 展开用）。"""
        if not resource_ids:
            return []
        result = await self._session.execute(
            select(CmdbRelatesTo).where(
                or_(
                    CmdbRelatesTo.source_id.in_(resource_ids),
                    CmdbRelatesTo.target_id.in_(resource_ids),
                )
            )
        )
        return list(result.scalars().all())
