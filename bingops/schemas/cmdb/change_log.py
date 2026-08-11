"""CMDB 变更记录 Pydantic 模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ChangeLogCreate(BaseModel):
    """创建变更记录请求（内部使用，不由 API 直接调用）。"""

    resource_id: int = Field(gt=0)
    resource_type: str = Field(min_length=1, max_length=64)
    change_type: str = Field(min_length=1, max_length=16, description="create | update | delete | relate | unrelate | tag")
    field: str | None = Field(default=None, max_length=128, description="变更字段名")
    old_value: str | None = Field(default=None, description="旧值")
    new_value: str | None = Field(default=None, description="新值")
    source: str = Field(default="discovery", description="变更来源")
    operator: str | None = Field(default=None, max_length=128, description="操作人")


class ChangeLogResponse(BaseModel):
    """变更记录响应。"""

    id: int
    resource_id: int
    resource_type: str
    change_type: str
    field: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    source: str
    operator: str | None = None
    created_at: datetime
