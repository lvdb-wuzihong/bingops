"""CMDB 标签 Pydantic 模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ── 标签定义 (Tag Definition) ─────────────────────────────────────────────────


class TagDefinitionCreate(BaseModel):
    """创建标签定义请求。"""

    tag_key: str = Field(min_length=1, max_length=128, description="标签 key，全局唯一")
    name: str = Field(min_length=1, max_length=256, description="标签显示名称")
    description: str | None = Field(default=None, description="标签描述")
    category: str = Field(default="custom", description="分类: system | cloud | custom")
    value_type: str = Field(default="string", description="值类型: string | enum | number | boolean")
    allowed_values: list[str] | None = Field(default=None, description="enum 类型时的可选值列表")
    editable: bool = Field(default=True, description="是否允许手动编辑")


class TagDefinitionUpdate(BaseModel):
    """更新标签定义请求。"""

    name: str | None = Field(default=None, max_length=256)
    description: str | None = None
    allowed_values: list[str] | None = None
    editable: bool | None = None


class TagDefinitionResponse(BaseModel):
    """标签定义响应。"""

    id: int
    tag_key: str
    name: str
    description: str | None = None
    category: str
    value_type: str
    allowed_values: list[str] | None = None
    editable: bool
    created_at: datetime
    updated_at: datetime


# ── 资源标签 (Resource Tag) ────────────────────────────────────────────────────


class ResourceTagCreate(BaseModel):
    """为资源打标签请求。"""

    resource_id: int = Field(gt=0, description="资源 ID")
    tag_key: str = Field(min_length=1, max_length=128, description="标签 key")
    tag_value: str = Field(min_length=1, description="标签值")
    source: str = Field(default="manual", description="来源: cloud | manual | rule")


class ResourceTagBatchCreate(BaseModel):
    """批量为资源打标签请求。"""

    resource_id: int = Field(gt=0, description="资源 ID")
    tags: list[ResourceTagCreate] = Field(min_length=1, description="标签列表")


class ResourceTagResponse(BaseModel):
    """资源标签响应。"""

    id: int
    resource_id: int
    tag_key: str
    tag_value: str
    source: str
    raw_key: str | None = None
    synced_at: datetime | None = None
    operator: str | None = None
    created_at: datetime
    updated_at: datetime
