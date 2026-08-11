"""CMDB 模型管理业务服务。"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from bingops.core.exceptions import NotFoundError, ValidationError
from bingops.models.cmdb.model import (
    CmdbModel,
    CmdbModelCategory,
    CmdbModelField,
    CmdbModelRelation,
    CmdbOptionSet,
)
from bingops.repositories.cmdb.model_repo import CmdbModelRepo
from bingops.schemas.cmdb.model import (
    ModelCategoryCreate,
    ModelCategoryUpdate,
    ModelCreate,
    ModelFieldCreate,
    ModelFieldUpdate,
    ModelRelationCreate,
    ModelUpdate,
    OptionSetCreate,
    OptionSetUpdate,
)

logger = logging.getLogger(f"bingops.{__name__}")

# ── 模型分类 ──────────────────────────────────────────────────────────────────


async def list_categories(session: AsyncSession):
    repo = CmdbModelRepo(session)
    return await repo.list_categories()


async def create_category(session: AsyncSession, payload: ModelCategoryCreate):
    repo = CmdbModelRepo(session)
    existing = await repo.get_category_by_code(payload.code)
    if existing:
        raise ValidationError(f"Category code '{payload.code}' already exists")
    category = CmdbModelCategory(**payload.model_dump())
    result = await repo.create_category(category)
    await session.commit()
    return result


async def update_category(session: AsyncSession, category_id: int, payload: ModelCategoryUpdate):
    repo = CmdbModelRepo(session)
    category = await repo.get_category(category_id)
    if not category:
        raise NotFoundError("Model category not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(category, key, value)
    result = await repo.update_category(category)
    await session.commit()
    return result


async def delete_category(session: AsyncSession, category_id: int):
    repo = CmdbModelRepo(session)
    category = await repo.get_category(category_id)
    if not category:
        raise NotFoundError("Model category not found")
    await repo.delete_category(category_id)
    await session.commit()


# ── 模型定义 ──────────────────────────────────────────────────────────────────


async def list_models(session: AsyncSession, category_id: int | None = None):
    repo = CmdbModelRepo(session)
    return await repo.list_models(category_id)


async def get_model(session: AsyncSession, model_id: int):
    repo = CmdbModelRepo(session)
    model = await repo.get_model(model_id)
    if not model:
        raise NotFoundError("Model not found")
    return model


async def create_model(session: AsyncSession, payload: ModelCreate):
    repo = CmdbModelRepo(session)
    # 检查分类存在
    category = await repo.get_category(payload.category_id)
    if not category:
        raise NotFoundError("Model category not found")
    # 检查 code 唯一
    existing = await repo.get_model_by_code(payload.code)
    if existing:
        raise ValidationError(f"Model code '{payload.code}' already exists")
    model = CmdbModel(**payload.model_dump())
    result = await repo.create_model(model)
    await session.commit()
    logger.info("Model created", extra={"model_code": payload.code})
    return result


async def update_model(session: AsyncSession, model_id: int, payload: ModelUpdate):
    repo = CmdbModelRepo(session)
    model = await repo.get_model(model_id)
    if not model:
        raise NotFoundError("Model not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(model, key, value)
    result = await repo.update_model(model)
    await session.commit()
    return result


async def delete_model(session: AsyncSession, model_id: int):
    repo = CmdbModelRepo(session)
    model = await repo.get_model(model_id)
    if not model:
        raise NotFoundError("Model not found")
    if model.is_builtin:
        raise ValidationError("Cannot delete built-in model")
    await repo.delete_model(model_id)
    await session.commit()
    logger.info("Model deleted", extra={"model_code": model.code})


# ── 字段定义 ──────────────────────────────────────────────────────────────────


async def list_fields(session: AsyncSession, model_id: int):
    repo = CmdbModelRepo(session)
    return await repo.list_fields(model_id)


async def create_field(session: AsyncSession, model_id: int, payload: ModelFieldCreate):
    repo = CmdbModelRepo(session)
    model = await repo.get_model(model_id)
    if not model:
        raise NotFoundError("Model not found")
    # 检查 code 在模型内唯一
    existing = await repo.get_field_by_code(model_id, payload.code)
    if existing:
        raise ValidationError(f"Field code '{payload.code}' already exists in this model")
    field = CmdbModelField(model_id=model_id, **payload.model_dump())
    result = await repo.create_field(field)
    await session.commit()
    return result


async def update_field(session: AsyncSession, model_id: int, field_id: int, payload: ModelFieldUpdate):
    repo = CmdbModelRepo(session)
    field = await repo.get_field(field_id)
    if not field or field.model_id != model_id:
        raise NotFoundError("Field not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(field, key, value)
    result = await repo.update_field(field)
    await session.commit()
    return result


async def delete_field(session: AsyncSession, model_id: int, field_id: int):
    repo = CmdbModelRepo(session)
    field = await repo.get_field(field_id)
    if not field or field.model_id != model_id:
        raise NotFoundError("Field not found")
    if field.is_builtin:
        raise ValidationError("Cannot delete built-in field")
    await repo.delete_field(field_id)
    await session.commit()


# ── 模型关系定义 ──────────────────────────────────────────────────────────────


async def list_model_relations(session: AsyncSession, model_id: int):
    repo = CmdbModelRepo(session)
    return await repo.list_model_relations(model_id)


async def get_models_by_ids(session: AsyncSession, model_ids: list[int]):
    repo = CmdbModelRepo(session)
    return await repo.get_models_by_ids(model_ids)


async def create_model_relation(session: AsyncSession, model_id: int, payload: ModelRelationCreate):
    repo = CmdbModelRepo(session)
    # 验证 source 和 target 模型存在
    source = await repo.get_model(model_id)
    if not source:
        raise NotFoundError("Source model not found")
    target = await repo.get_model(payload.target_model_id)
    if not target:
        raise NotFoundError("Target model not found")
    if payload.relation_type not in ("belongs_to", "relates_to"):
        raise ValidationError("relation_type must be 'belongs_to' or 'relates_to'")
    relation = CmdbModelRelation(
        source_model_id=model_id,
        **payload.model_dump(),
    )
    result = await repo.create_model_relation(relation)
    await session.commit()
    return result


async def delete_model_relation(session: AsyncSession, model_id: int, relation_id: int):
    repo = CmdbModelRepo(session)
    await repo.delete_model_relation(relation_id)
    await session.commit()


# ── 公共选项库 ────────────────────────────────────────────────────────────────


async def list_option_sets(session: AsyncSession):
    repo = CmdbModelRepo(session)
    return await repo.list_option_sets()


async def create_option_set(session: AsyncSession, payload: OptionSetCreate):
    repo = CmdbModelRepo(session)
    existing = await repo.get_option_set_by_code(payload.code)
    if existing:
        raise ValidationError(f"Option set code '{payload.code}' already exists")
    option_set = CmdbOptionSet(**payload.model_dump())
    result = await repo.create_option_set(option_set)
    await session.commit()
    return result


async def update_option_set(session: AsyncSession, option_set_id: int, payload: OptionSetUpdate):
    repo = CmdbModelRepo(session)
    option_set = await repo.get_option_set(option_set_id)
    if not option_set:
        raise NotFoundError("Option set not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(option_set, key, value)
    result = await repo.update_option_set(option_set)
    await session.commit()
    return result


async def delete_option_set(session: AsyncSession, option_set_id: int):
    repo = CmdbModelRepo(session)
    option_set = await repo.get_option_set(option_set_id)
    if not option_set:
        raise NotFoundError("Option set not found")
    await repo.delete_option_set(option_set_id)
    await session.commit()
