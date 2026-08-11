"""CMDB 模型管理 Pydantic Schemas。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ── 模型分类 ──────────────────────────────────────────────────────────────────


class ModelCategoryCreate(BaseModel):
    """创建模型分类。"""

    name: str = Field(min_length=1, max_length=128, description="分类显示名")
    code: str = Field(min_length=1, max_length=64, description="分类编码（唯一）")
    icon: str | None = Field(default=None, max_length=64, description="图标标识")
    sort_order: int = Field(default=0, description="排序")


class ModelCategoryUpdate(BaseModel):
    """更新模型分类。"""

    name: str | None = Field(default=None, max_length=128)
    icon: str | None = Field(default=None, max_length=64)
    sort_order: int | None = None


class ModelCategoryResponse(BaseModel):
    """模型分类响应。"""

    id: int
    name: str
    code: str
    icon: str | None = None
    sort_order: int
    created_at: datetime
    updated_at: datetime


# ── 字段定义 ──────────────────────────────────────────────────────────────────


class ModelFieldCreate(BaseModel):
    """创建字段定义。"""

    name: str = Field(min_length=1, max_length=128, description="字段显示名")
    code: str = Field(min_length=1, max_length=64, description="字段编码")
    field_type: str = Field(description="字段类型: string|number|boolean|date|datetime|enum|multi_enum|password|json")
    group_name: str | None = Field(default=None, max_length=64, description="字段分组")
    is_required: bool = False
    is_unique: bool = False
    is_searchable: bool = True
    default_value: str | None = None
    placeholder: str | None = Field(default=None, max_length=256)
    options: list[dict] | None = Field(default=None, description="枚举选项")
    option_set_id: int | None = Field(default=None, deprecated=True, description="【已下线】公共选项库引用，枚举一律用内联 options，请勿传入")
    sort_order: int = 0


class ModelFieldUpdate(BaseModel):
    """更新字段定义。"""

    name: str | None = Field(default=None, max_length=128)
    group_name: str | None = Field(default=None, max_length=64)
    is_required: bool | None = None
    is_unique: bool | None = None
    is_searchable: bool | None = None
    default_value: str | None = None
    placeholder: str | None = None
    options: list[dict] | None = None
    option_set_id: int | None = None
    sort_order: int | None = None


class ModelFieldResponse(BaseModel):
    """字段定义响应。"""

    id: int
    model_id: int
    name: str
    code: str
    field_type: str
    group_name: str | None = None
    is_required: bool
    is_unique: bool
    is_searchable: bool
    is_builtin: bool
    default_value: str | None = None
    placeholder: str | None = None
    options: list[dict] | None = None
    option_set_id: int | None = None
    sort_order: int
    created_at: datetime
    updated_at: datetime


# ── 模型定义 ──────────────────────────────────────────────────────────────────


class ModelCreate(BaseModel):
    """创建模型。"""

    category_id: int = Field(description="所属分类 ID")
    name: str = Field(min_length=1, max_length=128, description="模型显示名")
    code: str = Field(min_length=1, max_length=64, description="模型编码（唯一）")
    icon: str | None = Field(default=None, max_length=64)
    description: str | None = None
    sort_order: int = 0


class ModelUpdate(BaseModel):
    """更新模型。"""

    name: str | None = Field(default=None, max_length=128)
    icon: str | None = Field(default=None, max_length=64)
    description: str | None = None
    is_enabled: bool | None = None
    sort_order: int | None = None


class ModelResponse(BaseModel):
    """模型响应（含字段列表）。"""

    id: int
    category_id: int
    name: str
    code: str
    icon: str | None = None
    description: str | None = None
    is_builtin: bool
    is_enabled: bool
    sort_order: int
    fields: list[ModelFieldResponse] = Field(default_factory=list)
    instance_count: int = Field(default=0, description="实例数量")
    created_at: datetime
    updated_at: datetime


class ModelListResponse(BaseModel):
    """模型列表响应（不含字段详情）。"""

    id: int
    category_id: int
    name: str
    code: str
    icon: str | None = None
    is_builtin: bool
    is_enabled: bool
    sort_order: int
    instance_count: int = 0
    created_at: datetime
    updated_at: datetime


# ── 模型关系定义 ──────────────────────────────────────────────────────────────


class ModelRelationCreate(BaseModel):
    """创建模型关系定义。"""

    target_model_id: int = Field(description="目标模型 ID")
    relation_type: str = Field(description="关系类型: belongs_to | relates_to")
    relation_name: str | None = Field(default=None, max_length=128, description="关系显示名")
    description: str | None = None


class ModelRelationResponse(BaseModel):
    """模型关系定义响应。"""

    id: int
    source_model_id: int
    target_model_id: int
    source_model_code: str | None = None
    source_model_name: str | None = None
    target_model_code: str | None = None
    target_model_name: str | None = None
    relation_type: str
    relation_name: str | None = None
    description: str | None = None
    created_at: datetime


# ── 公共选项库 ────────────────────────────────────────────────────────────────


class OptionSetCreate(BaseModel):
    """创建公共选项集。"""

    name: str = Field(min_length=1, max_length=128, description="显示名")
    code: str = Field(min_length=1, max_length=64, description="编码（唯一）")
    options: list[dict] = Field(description='选项列表 [{"label":"...","value":"..."}]')


class OptionSetUpdate(BaseModel):
    """更新公共选项集。"""

    name: str | None = Field(default=None, max_length=128)
    options: list[dict] | None = None


class OptionSetResponse(BaseModel):
    """公共选项集响应。"""

    id: int
    name: str
    code: str
    options: list[dict]
    created_at: datetime
    updated_at: datetime


# ── 分类树响应（含嵌套模型） ──────────────────────────────────────────────────


class CategoryTreeResponse(BaseModel):
    """分类树节点（含嵌套模型列表）。"""

    id: int
    name: str
    code: str
    icon: str | None = None
    sort_order: int
    models: list[ModelListResponse] = Field(default_factory=list)
