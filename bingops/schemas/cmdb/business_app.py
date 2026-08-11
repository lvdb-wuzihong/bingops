"""CMDB 业务应用 Pydantic 模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class BusinessAppCreate(BaseModel):
    """创建业务应用请求。"""

    app_code: str = Field(min_length=1, max_length=64, description="应用编码，全局唯一")
    name: str = Field(min_length=1, max_length=256, description="应用名称")
    description: str | None = Field(default=None, description="应用描述")
    team: str | None = Field(default=None, max_length=128, description="所属团队")
    owner: str | None = Field(default=None, max_length=128, description="负责人")
    department: str | None = Field(default=None, max_length=128, description="所属部门")
    labels: dict = Field(default_factory=dict, description="应用级标签")


class BusinessAppUpdate(BaseModel):
    """更新业务应用请求。"""

    name: str | None = Field(default=None, max_length=256)
    description: str | None = None
    team: str | None = Field(default=None, max_length=128)
    owner: str | None = Field(default=None, max_length=128)
    department: str | None = Field(default=None, max_length=128)
    labels: dict | None = None


class BusinessAppResponse(BaseModel):
    """业务应用响应。"""

    id: int
    app_code: str
    name: str
    description: str | None = None
    team: str | None = None
    owner: str | None = None
    department: str | None = None
    labels: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
