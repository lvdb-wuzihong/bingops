"""凭据目录 Pydantic 模型（API 契约）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CredentialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    kind: str = Field(description="ssh_key | cloud_ak | db_password | api_token | kubeconfig")
    # v36：入口只一个框——运维熟悉的 Vault 引用形状 `path` 或 `path#field`。
    # 早期拆成 vault_path + vault_field 两个输入框，反馈是“不知道哪个才是 Vault”；
    # 存储仍拆两列（服务层 partition），API 与表单不拆
    vault_ref: str = Field(min_length=1, max_length=640)
    remark: str | None = None


class CredentialUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    kind: str | None = None
    vault_ref: str | None = Field(default=None, min_length=1, max_length=640)
    remark: str | None = None
    is_active: bool | None = None


class CredentialResponse(BaseModel):
    id: int
    name: str
    kind: str
    # 与入参同形：path 或 path#field（runner 消费的就是这个串）
    vault_ref: str
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
