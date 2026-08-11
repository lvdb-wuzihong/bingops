"""角色与权限管理 Pydantic 模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PermissionResponse(BaseModel):
    """权限信息响应。"""

    id: int
    code: str
    name: str
    description: str | None = None


class RoleCreate(BaseModel):
    """创建角色请求。"""

    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None


class RoleUpdate(BaseModel):
    """更新角色请求。"""

    name: str | None = Field(default=None, max_length=128)
    description: str | None = None


class RoleResponse(BaseModel):
    """角色信息响应。"""

    id: int
    code: str
    name: str
    description: str | None = None
    is_system: bool
    created_at: datetime
    updated_at: datetime
    permissions: list[str] = Field(default_factory=list, description="权限 code 列表")


class RolePermissionAssign(BaseModel):
    """分配权限请求。"""

    permission_codes: list[str] = Field(min_length=1, description="权限 code 列表，全量替换")
