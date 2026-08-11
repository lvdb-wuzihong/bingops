"""用户管理 Pydantic 模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    """创建用户请求。"""

    username: str = Field(min_length=1, max_length=64)
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    display_name: str | None = Field(default=None, max_length=128)
    is_superuser: bool = False


class UserUpdate(BaseModel):
    """更新用户请求（所有字段可选）。"""

    email: EmailStr | None = None
    display_name: str | None = None
    is_active: bool | None = None
    is_superuser: bool | None = None


class UserResponse(BaseModel):
    """用户信息响应。"""

    id: int
    username: str
    email: str
    display_name: str | None = None
    avatar_url: str | None = None
    auth_source: str
    is_active: bool
    is_superuser: bool
    last_login_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    roles: list[str] = Field(default_factory=list, description="角色 code 列表")


class UserRoleAssign(BaseModel):
    """分配角色请求。"""

    role_codes: list[str] = Field(min_length=1, description="角色 code 列表，全量替换")
