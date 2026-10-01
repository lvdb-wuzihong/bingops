"""中转网关 Pydantic 模型（API 契约）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class GatewayScope(BaseModel):
    """选择维度：命中任一即视为该网关可服务这台机器。"""

    vpc_ids: list[str] = Field(default_factory=list)
    cloud_accounts: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    resource_ids: list[int] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.vpc_ids or self.cloud_accounts or self.regions or self.resource_ids
        )


class GatewayCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    host: str = Field(min_length=1, max_length=128)
    port: int = Field(default=22, ge=1, le=65535)
    login_user: str = Field(default="root", max_length=64)
    # 引用 credentials.name（kind=ssh_key）；留空表示网关用目标机同一把钥匙
    ssh_credential: str | None = Field(default=None, max_length=128)
    scope: GatewayScope = Field(default_factory=GatewayScope)
    priority: int = Field(default=100, ge=0, le=1000)
    remark: str | None = None


class GatewayUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    host: str | None = Field(default=None, min_length=1, max_length=128)
    port: int | None = Field(default=None, ge=1, le=65535)
    login_user: str | None = Field(default=None, max_length=64)
    ssh_credential: str | None = Field(default=None, max_length=128)
    scope: GatewayScope | None = None
    priority: int | None = Field(default=None, ge=0, le=1000)
    remark: str | None = None
    is_active: bool | None = None


class GatewayResponse(BaseModel):
    id: int
    name: str
    host: str
    port: int
    login_user: str
    ssh_credential: str | None
    scope: dict
    priority: int
    remark: str | None
    is_active: bool
    created_by: int | None
    created_at: datetime
    updated_at: datetime


class HostReachability(BaseModel):
    """可达性视图的一行：这台机器能不能被任务访问到、走哪条路、缺什么。"""

    resource_id: int
    name: str
    ip: str | None = None
    model_code: str | None = None
    cloud_account: str | None = None
    region: str | None = None
    vpc_id: str | None = None
    # 登录身份（v33：属于主机标签，跨用户环境同一把钥匙可对应多个用户）
    login_user: str | None = None
    # 凭据是否解析得到（v31 目录 + 主机标签）
    credential: str | None = None
    credential_ok: bool = False
    # 走哪个网关；None 且 gateway_ok=True 表示直连
    gateway: str | None = None
    gateway_ok: bool = True
    # 缺口原因（供"缺什么"看板直接展示，不需要前端再推断）
    missing: list[str] = Field(default_factory=list)
