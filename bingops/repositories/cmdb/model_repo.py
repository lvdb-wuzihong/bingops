"""CMDB 模型管理数据访问层。"""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.models.cmdb.model import (
    CmdbModel,
    CmdbModelCategory,
    CmdbModelField,
    CmdbModelRelation,
    CmdbOptionSet,
)


class CmdbModelRepo:
    """模型管理 Repository。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── 模型分类 ──────────────────────────────────────────────────────────────

    async def list_categories(self) -> list[CmdbModelCategory]:
        result = await self._session.execute(
            select(CmdbModelCategory).order_by(CmdbModelCategory.sort_order)
        )
        return list(result.scalars().all())

    async def get_category(self, category_id: int) -> CmdbModelCategory | None:
        result = await self._session.execute(
            select(CmdbModelCategory).where(CmdbModelCategory.id == category_id)
        )
        return result.scalar_one_or_none()

    async def get_category_by_code(self, code: str) -> CmdbModelCategory | None:
        result = await self._session.execute(
            select(CmdbModelCategory).where(CmdbModelCategory.code == code)
        )
        return result.scalar_one_or_none()

    async def create_category(self, category: CmdbModelCategory) -> CmdbModelCategory:
        self._session.add(category)
        await self._session.flush()
        return category

    async def update_category(self, category: CmdbModelCategory) -> CmdbModelCategory:
        await self._session.flush()
        return category

    async def delete_category(self, category_id: int) -> None:
        await self._session.execute(
            delete(CmdbModelCategory).where(CmdbModelCategory.id == category_id)
        )

    # ── 模型定义 ──────────────────────────────────────────────────────────────

    async def list_models(self, category_id: int | None = None) -> list[CmdbModel]:
        query = select(CmdbModel).order_by(CmdbModel.sort_order)
        if category_id:
            query = query.where(CmdbModel.category_id == category_id)
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def get_model(self, model_id: int) -> CmdbModel | None:
        result = await self._session.execute(
            select(CmdbModel).where(CmdbModel.id == model_id)
        )
        return result.scalar_one_or_none()

    async def get_model_by_code(self, code: str) -> CmdbModel | None:
        result = await self._session.execute(
            select(CmdbModel).where(CmdbModel.code == code)
        )
        return result.scalar_one_or_none()

    async def create_model(self, model: CmdbModel) -> CmdbModel:
        self._session.add(model)
        await self._session.flush()
        return model

    async def update_model(self, model: CmdbModel) -> CmdbModel:
        await self._session.flush()
        return model

    async def delete_model(self, model_id: int) -> None:
        await self._session.execute(
            delete(CmdbModel).where(CmdbModel.id == model_id)
        )

    # ── 字段定义 ──────────────────────────────────────────────────────────────

    async def list_fields(self, model_id: int) -> list[CmdbModelField]:
        result = await self._session.execute(
            select(CmdbModelField)
            .where(CmdbModelField.model_id == model_id)
            .order_by(CmdbModelField.sort_order)
        )
        return list(result.scalars().all())

    async def get_field(self, field_id: int) -> CmdbModelField | None:
        result = await self._session.execute(
            select(CmdbModelField).where(CmdbModelField.id == field_id)
        )
        return result.scalar_one_or_none()

    async def get_field_by_code(self, model_id: int, code: str) -> CmdbModelField | None:
        result = await self._session.execute(
            select(CmdbModelField).where(
                CmdbModelField.model_id == model_id,
                CmdbModelField.code == code,
            )
        )
        return result.scalar_one_or_none()

    async def create_field(self, field: CmdbModelField) -> CmdbModelField:
        self._session.add(field)
        await self._session.flush()
        return field

    async def update_field(self, field: CmdbModelField) -> CmdbModelField:
        await self._session.flush()
        return field

    async def delete_field(self, field_id: int) -> None:
        await self._session.execute(
            delete(CmdbModelField).where(CmdbModelField.id == field_id)
        )

    # ── 模型关系定义 ──────────────────────────────────────────────────────────

    async def list_model_relations(self, model_id: int) -> list[CmdbModelRelation]:
        """查询某模型相关的所有关系定义（作为 source 或 target）。"""
        result = await self._session.execute(
            select(CmdbModelRelation).where(
                (CmdbModelRelation.source_model_id == model_id)
                | (CmdbModelRelation.target_model_id == model_id)
            )
        )
        return list(result.scalars().all())

    async def get_models_by_ids(self, model_ids: list[int]) -> list[CmdbModel]:
        """批量查询模型（关系列表渲染对端名称用）。"""
        if not model_ids:
            return []
        result = await self._session.execute(
            select(CmdbModel).where(CmdbModel.id.in_(model_ids))
        )
        return list(result.scalars().all())

    async def create_model_relation(self, relation: CmdbModelRelation) -> CmdbModelRelation:
        self._session.add(relation)
        await self._session.flush()
        return relation

    async def delete_model_relation(self, relation_id: int) -> None:
        await self._session.execute(
            delete(CmdbModelRelation).where(CmdbModelRelation.id == relation_id)
        )

    # ── 公共选项库 ────────────────────────────────────────────────────────────

    async def list_option_sets(self) -> list[CmdbOptionSet]:
        result = await self._session.execute(
            select(CmdbOptionSet).order_by(CmdbOptionSet.name)
        )
        return list(result.scalars().all())

    async def get_option_set(self, option_set_id: int) -> CmdbOptionSet | None:
        result = await self._session.execute(
            select(CmdbOptionSet).where(CmdbOptionSet.id == option_set_id)
        )
        return result.scalar_one_or_none()

    async def get_option_set_by_code(self, code: str) -> CmdbOptionSet | None:
        result = await self._session.execute(
            select(CmdbOptionSet).where(CmdbOptionSet.code == code)
        )
        return result.scalar_one_or_none()

    async def create_option_set(self, option_set: CmdbOptionSet) -> CmdbOptionSet:
        self._session.add(option_set)
        await self._session.flush()
        return option_set

    async def update_option_set(self, option_set: CmdbOptionSet) -> CmdbOptionSet:
        await self._session.flush()
        return option_set

    async def delete_option_set(self, option_set_id: int) -> None:
        await self._session.execute(
            delete(CmdbOptionSet).where(CmdbOptionSet.id == option_set_id)
        )
