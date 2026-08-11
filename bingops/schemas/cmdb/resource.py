"""CMDB 资源实例 Pydantic Schemas（v2 动态模型）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ResourceCreate(BaseModel):
    """创建资源实例请求。"""

    model_id: int = Field(description="所属模型 ID")
    name: str = Field(min_length=1, max_length=256, description="实例名称")
    provider: str | None = Field(default=None, max_length=32, description="云厂商: aliyun|aws|gcp|k8s|manual")
    provider_id: str | None = Field(default=None, max_length=256, description="云厂商原始 ID")
    cloud_account: str | None = Field(default=None, max_length=128, description="云账号标识")
    region: str | None = Field(default=None, max_length=64)
    zone: str | None = Field(default=None, max_length=64)
    status: str = Field(default="unknown", max_length=32)
    fields: dict = Field(default_factory=dict, description="动态字段值")


class ResourceUpdate(BaseModel):
    """更新资源实例请求（所有字段可选）。"""

    name: str | None = Field(default=None, max_length=256)
    status: str | None = Field(default=None, max_length=32)
    region: str | None = Field(default=None, max_length=64)
    zone: str | None = Field(default=None, max_length=64)
    fields: dict | None = None


class ResourceResponse(BaseModel):
    """资源实例响应。"""

    id: int
    model_id: int
    name: str
    provider: str | None = None
    provider_id: str | None = None
    cloud_account: str | None = None
    region: str | None = None
    zone: str | None = None
    status: str
    fields: dict = Field(default_factory=dict)
    resource_version: str | None = None
    synced_at: datetime | None = None
    source: str
    created_at: datetime
    updated_at: datetime


class ResourceListQuery(BaseModel):
    """资源列表查询参数。"""

    model_id: int | None = None
    provider: str | None = None
    status: str | None = None
    cloud_account: str | None = None
    region: str | None = None
    keyword: str | None = None
    tag_key: str | None = None
    tag_value: str | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class ResourceStatsResponse(BaseModel):
    """资源统计响应。"""

    total: int
    by_model: dict[str, int] = Field(default_factory=dict, description="按模型统计")
    by_status: dict[str, int] = Field(default_factory=dict, description="按状态统计")
    by_provider: dict[str, int] = Field(default_factory=dict, description="按云厂商统计")
