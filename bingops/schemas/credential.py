"""凭据目录 Pydantic 模型（API 契约）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CredentialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    kind: str = Field(description="ssh_key | cloud_ak | db_password | api_token | kubeconfig")
    # 只存引用：Vault 路径（+ 可选字段名），任何字段都不得出现明文凭据值
    vault_path: str = Field(min_length=1, max_length=512)
    vault_field: str | None = Field(default=None, max_length=128)
    login_user: str | None = Field(default=None, max_length=64)
    cloud_account: str | None = Field(default=None, max_length=128)
    region: str | None = Field(default=None, max_length=64)
    is_default: bool = False
    remark: str | None = None


class CredentialUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    kind: str | None = None
    vault_path: str | None = Field(default=None, min_length=1, max_length=512)
    vault_field: str | None = Field(default=None, max_length=128)
    login_user: str | None = Field(default=None, max_length=64)
    cloud_account: str | None = Field(default=None, max_length=128)
    region: str | None = Field(default=None, max_length=64)
    is_default: bool | None = None
    remark: str | None = None
    is_active: bool | None = None


class CredentialResponse(BaseModel):
    id: int
    name: str
    kind: str
    login_user: str | None
    vault_path: str
    vault_field: str | None
    cloud_account: str | None
    region: str | None
    is_default: bool
    # 探测状态由 runner 回填（bingops 不直连 Vault）：unknown | ok | failed
    verify_state: str
    last_verified_at: datetime | None
    remark: str | None
    is_active: bool
    created_by: int | None
    created_at: datetime
    updated_at: datetime


class CredentialUsageResponse(BaseModel):
    """凭据引用反查：换钥匙/删条目之前必须先看清影响面。"""

    credential_id: int
    credential_name: str
    host_tag_refs: int = Field(description="主机标签 ssh_credential 引用它的台数")
    runbook_refs: int = Field(description="connection.secrets_schema 里引用它的 runbook 数")
    hosts: list[dict] = Field(default_factory=list, description="引用它的主机（最多 50 条）")
