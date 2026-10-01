"""中转网关 Pydantic 模型（API 契约）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class GatewayCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    host: str = Field(min_length=1, max_length=128)
    port: int = Field(default=22, ge=1, le=65535)
    login_user: str = Field(default="root", max_length=64)
    # 引用 credentials.name（kind=ssh_key）；留空表示网关用目标机同一把钥匙
    ssh_credential: str | None = Field(default=None, max_length=128)
    # 本网关接管哪些 VPC（v35 唯一关联维度）：前端应下拉选 CMDB 里的
    # aliyun_vpc / gcp_vpc，不让人手打 VPC ID
    vpc_ids: list[str] = Field(default_factory=list)
    remark: str | None = None


class GatewayUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    host: str | None = Field(default=None, min_length=1, max_length=128)
    port: int | None = Field(default=None, ge=1, le=65535)
    login_user: str | None = Field(default=None, max_length=64)
    ssh_credential: str | None = Field(default=None, max_length=128)
    vpc_ids: list[str] | None = None
    remark: str | None = None
    is_active: bool | None = None


class GatewayResponse(BaseModel):
    id: int
    name: str
    host: str
    port: int
    login_user: str
    ssh_credential: str | None
    vpc_ids: list
    remark: str | None
    is_active: bool
    created_by: int | None
    created_at: datetime
    updated_at: datetime
