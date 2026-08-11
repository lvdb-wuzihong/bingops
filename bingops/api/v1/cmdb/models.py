"""CMDB 模型管理 API 路由（v2 动态模型）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.api.dependencies import get_db_session, require_permission
from bingops.core.response import success_response
from bingops.models.user import User
from bingops.schemas.cmdb.model import (
    ModelCategoryCreate,
    ModelCategoryResponse,
    ModelCategoryUpdate,
    ModelCreate,
    ModelFieldCreate,
    ModelFieldResponse,
    ModelFieldUpdate,
    ModelListResponse,
    ModelRelationCreate,
    ModelRelationResponse,
    ModelResponse,
    ModelUpdate,
    OptionSetCreate,
    OptionSetResponse,
    OptionSetUpdate,
)
from bingops.services.cmdb import model_service

router = APIRouter(prefix="/api/v1/cmdb/models", tags=["cmdb-models"])


# ── 模型分类 ──────────────────────────────────────────────────────────────────


@router.get("/categories")
async def list_categories(
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_model:list"),
):
    """查询所有模型分类。"""
    categories = await model_service.list_categories(session)
    items = [
        ModelCategoryResponse(
            id=c.id, name=c.name, code=c.code, icon=c.icon,
            sort_order=c.sort_order, created_at=c.created_at, updated_at=c.updated_at,
        ).model_dump(mode="json")
        for c in categories
    ]
    return success_response(data=items)


@router.post("/categories", status_code=201)
async def create_category(
    payload: ModelCategoryCreate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_model:create"),
):
    """创建模型分类。"""
    category = await model_service.create_category(session, payload)
    data = ModelCategoryResponse(
        id=category.id, name=category.name, code=category.code, icon=category.icon,
        sort_order=category.sort_order, created_at=category.created_at, updated_at=category.updated_at,
    )
    return success_response(data=data.model_dump(mode="json"), message="Category created", http_status=201)


@router.put("/categories/{category_id}")
async def update_category(
    category_id: int,
    payload: ModelCategoryUpdate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_model:update"),
):
    """更新模型分类。"""
    category = await model_service.update_category(session, category_id, payload)
    data = ModelCategoryResponse(
        id=category.id, name=category.name, code=category.code, icon=category.icon,
        sort_order=category.sort_order, created_at=category.created_at, updated_at=category.updated_at,
    )
    return success_response(data=data.model_dump(mode="json"))


@router.delete("/categories/{category_id}")
async def delete_category(
    category_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_model:delete"),
):
    """删除模型分类。"""
    await model_service.delete_category(session, category_id)
    return success_response(message="Category deleted")


# ── 公共选项库 ────────────────────────────────────────────────────────────────
# 注意：必须注册在 GET /{model_id} 之前，否则 "option-sets" 会被当作 model_id 解析导致 422


@router.get("/option-sets", deprecated=True)
async def list_option_sets(
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_model:list"),
):
    """【已下线】查询公共选项库列表。

    枚举字段一律使用字段内联 options，不再使用公共选项库；本组接口休眠保留，前端请勿接入。
    """
    option_sets = await model_service.list_option_sets(session)
    items = [
        OptionSetResponse(
            id=o.id, name=o.name, code=o.code, options=o.options,
            created_at=o.created_at, updated_at=o.updated_at,
        ).model_dump(mode="json")
        for o in option_sets
    ]
    return success_response(data=items)


@router.post("/option-sets", status_code=201, deprecated=True)
async def create_option_set(
    payload: OptionSetCreate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_model:create"),
):
    """【已下线】创建公共选项集。

    枚举字段一律使用字段内联 options，不再使用公共选项库；本组接口休眠保留，前端请勿接入。
    """
    option_set = await model_service.create_option_set(session, payload)
    data = OptionSetResponse(
        id=option_set.id, name=option_set.name, code=option_set.code,
        options=option_set.options,
        created_at=option_set.created_at, updated_at=option_set.updated_at,
    )
    return success_response(data=data.model_dump(mode="json"), message="Option set created", http_status=201)


@router.put("/option-sets/{option_set_id}", deprecated=True)
async def update_option_set(
    option_set_id: int,
    payload: OptionSetUpdate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_model:update"),
):
    """【已下线】更新公共选项集。

    枚举字段一律使用字段内联 options，不再使用公共选项库；本组接口休眠保留，前端请勿接入。
    """
    option_set = await model_service.update_option_set(session, option_set_id, payload)
    data = OptionSetResponse(
        id=option_set.id, name=option_set.name, code=option_set.code,
        options=option_set.options,
        created_at=option_set.created_at, updated_at=option_set.updated_at,
    )
    return success_response(data=data.model_dump(mode="json"))


@router.delete("/option-sets/{option_set_id}", deprecated=True)
async def delete_option_set(
    option_set_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_model:delete"),
):
    """【已下线】删除公共选项集。

    枚举字段一律使用字段内联 options，不再使用公共选项库；本组接口休眠保留，前端请勿接入。
    """
    await model_service.delete_option_set(session, option_set_id)
    return success_response(message="Option set deleted")


# ── 模型定义 ──────────────────────────────────────────────────────────────────


@router.get("")
async def list_models(
    category_id: int | None = None,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_model:list"),
):
    """查询模型列表。"""
    models = await model_service.list_models(session, category_id)
    items = [
        ModelListResponse(
            id=m.id, category_id=m.category_id, name=m.name, code=m.code,
            icon=m.icon, is_builtin=m.is_builtin, is_enabled=m.is_enabled,
            sort_order=m.sort_order, instance_count=0,
            created_at=m.created_at, updated_at=m.updated_at,
        ).model_dump(mode="json")
        for m in models
    ]
    return success_response(data=items)


@router.get("/{model_id}")
async def get_model(
    model_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_model:list"),
):
    """获取模型详情（含字段列表）。"""
    model = await model_service.get_model(session, model_id)
    fields = await model_service.list_fields(session, model_id)
    data = ModelResponse(
        id=model.id, category_id=model.category_id, name=model.name, code=model.code,
        icon=model.icon, description=model.description, is_builtin=model.is_builtin,
        is_enabled=model.is_enabled, sort_order=model.sort_order,
        fields=[
            ModelFieldResponse(
                id=f.id, model_id=f.model_id, name=f.name, code=f.code,
                field_type=f.field_type, group_name=f.group_name,
                is_required=f.is_required, is_unique=f.is_unique,
                is_searchable=f.is_searchable, is_builtin=f.is_builtin,
                default_value=f.default_value, placeholder=f.placeholder,
                options=f.options, option_set_id=f.option_set_id,
                sort_order=f.sort_order, created_at=f.created_at, updated_at=f.updated_at,
            )
            for f in fields
        ],
        instance_count=0,
        created_at=model.created_at, updated_at=model.updated_at,
    )
    return success_response(data=data.model_dump(mode="json"))


@router.post("", status_code=201)
async def create_model(
    payload: ModelCreate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_model:create"),
):
    """创建模型。"""
    model = await model_service.create_model(session, payload)
    data = ModelListResponse(
        id=model.id, category_id=model.category_id, name=model.name, code=model.code,
        icon=model.icon, is_builtin=model.is_builtin, is_enabled=model.is_enabled,
        sort_order=model.sort_order, instance_count=0,
        created_at=model.created_at, updated_at=model.updated_at,
    )
    return success_response(data=data.model_dump(mode="json"), message="Model created", http_status=201)


@router.put("/{model_id}")
async def update_model(
    model_id: int,
    payload: ModelUpdate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_model:update"),
):
    """更新模型。"""
    model = await model_service.update_model(session, model_id, payload)
    data = ModelListResponse(
        id=model.id, category_id=model.category_id, name=model.name, code=model.code,
        icon=model.icon, is_builtin=model.is_builtin, is_enabled=model.is_enabled,
        sort_order=model.sort_order, instance_count=0,
        created_at=model.created_at, updated_at=model.updated_at,
    )
    return success_response(data=data.model_dump(mode="json"))


@router.delete("/{model_id}")
async def delete_model(
    model_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_model:delete"),
):
    """删除模型（内置模型不可删除）。"""
    await model_service.delete_model(session, model_id)
    return success_response(message="Model deleted")


# ── 字段定义 ──────────────────────────────────────────────────────────────────


@router.get("/{model_id}/fields")
async def list_fields(
    model_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_model:list"),
):
    """查询模型字段列表。"""
    fields = await model_service.list_fields(session, model_id)
    items = [
        ModelFieldResponse(
            id=f.id, model_id=f.model_id, name=f.name, code=f.code,
            field_type=f.field_type, group_name=f.group_name,
            is_required=f.is_required, is_unique=f.is_unique,
            is_searchable=f.is_searchable, is_builtin=f.is_builtin,
            default_value=f.default_value, placeholder=f.placeholder,
            options=f.options, option_set_id=f.option_set_id,
            sort_order=f.sort_order, created_at=f.created_at, updated_at=f.updated_at,
        ).model_dump(mode="json")
        for f in fields
    ]
    return success_response(data=items)


@router.post("/{model_id}/fields", status_code=201)
async def create_field(
    model_id: int,
    payload: ModelFieldCreate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_model:create"),
):
    """创建模型字段。"""
    field = await model_service.create_field(session, model_id, payload)
    data = ModelFieldResponse(
        id=field.id, model_id=field.model_id, name=field.name, code=field.code,
        field_type=field.field_type, group_name=field.group_name,
        is_required=field.is_required, is_unique=field.is_unique,
        is_searchable=field.is_searchable, is_builtin=field.is_builtin,
        default_value=field.default_value, placeholder=field.placeholder,
        options=field.options, option_set_id=field.option_set_id,
        sort_order=field.sort_order, created_at=field.created_at, updated_at=field.updated_at,
    )
    return success_response(data=data.model_dump(mode="json"), message="Field created", http_status=201)


@router.put("/{model_id}/fields/{field_id}")
async def update_field(
    model_id: int,
    field_id: int,
    payload: ModelFieldUpdate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_model:update"),
):
    """更新模型字段。"""
    field = await model_service.update_field(session, model_id, field_id, payload)
    data = ModelFieldResponse(
        id=field.id, model_id=field.model_id, name=field.name, code=field.code,
        field_type=field.field_type, group_name=field.group_name,
        is_required=field.is_required, is_unique=field.is_unique,
        is_searchable=field.is_searchable, is_builtin=field.is_builtin,
        default_value=field.default_value, placeholder=field.placeholder,
        options=field.options, option_set_id=field.option_set_id,
        sort_order=field.sort_order, created_at=field.created_at, updated_at=field.updated_at,
    )
    return success_response(data=data.model_dump(mode="json"))


@router.delete("/{model_id}/fields/{field_id}")
async def delete_field(
    model_id: int,
    field_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_model:delete"),
):
    """删除模型字段（内置字段不可删除）。"""
    await model_service.delete_field(session, model_id, field_id)
    return success_response(message="Field deleted")


# ── 模型关系定义 ──────────────────────────────────────────────────────────────


@router.get("/{model_id}/relations")
async def list_model_relations(
    model_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_model:list"),
):
    """查询模型关系定义列表。"""
    relations = await model_service.list_model_relations(session, model_id)
    model_ids = {r.source_model_id for r in relations} | {r.target_model_id for r in relations}
    models = {m.id: m for m in await model_service.get_models_by_ids(session, list(model_ids))}
    items = [
        ModelRelationResponse(
            id=r.id, source_model_id=r.source_model_id, target_model_id=r.target_model_id,
            source_model_code=models[r.source_model_id].code,
            source_model_name=models[r.source_model_id].name,
            target_model_code=models[r.target_model_id].code,
            target_model_name=models[r.target_model_id].name,
            relation_type=r.relation_type, relation_name=r.relation_name,
            description=r.description,
            created_at=r.created_at,
        ).model_dump(mode="json")
        for r in relations
    ]
    return success_response(data=items)


@router.post("/{model_id}/relations", status_code=201)
async def create_model_relation(
    model_id: int,
    payload: ModelRelationCreate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_model:create"),
):
    """创建模型关系定义。"""
    relation = await model_service.create_model_relation(session, model_id, payload)
    models = {m.id: m for m in await model_service.get_models_by_ids(
        session, [relation.source_model_id, relation.target_model_id])}
    data = ModelRelationResponse(
        id=relation.id, source_model_id=relation.source_model_id,
        target_model_id=relation.target_model_id,
        source_model_code=models[relation.source_model_id].code,
        source_model_name=models[relation.source_model_id].name,
        target_model_code=models[relation.target_model_id].code,
        target_model_name=models[relation.target_model_id].name,
        relation_type=relation.relation_type, relation_name=relation.relation_name,
        description=relation.description, created_at=relation.created_at,
    )
    return success_response(data=data.model_dump(mode="json"), message="Relation created", http_status=201)


@router.delete("/{model_id}/relations/{relation_id}")
async def delete_model_relation(
    model_id: int,
    relation_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_model:delete"),
):
    """删除模型关系定义。"""
    await model_service.delete_model_relation(session, model_id, relation_id)
    return success_response(message="Relation deleted")
