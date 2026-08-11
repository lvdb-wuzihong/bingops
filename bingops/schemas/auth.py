"""认证相关 Pydantic 模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """本地登录请求。"""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=128)


class TokenResponse(BaseModel):
    """JWT Token 响应。"""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="access_token 有效期（秒）")


class RefreshRequest(BaseModel):
    """刷新 Token 请求。"""

    refresh_token: str


class FeishuLoginResponse(BaseModel):
    """飞书登录 - 返回授权页 URL。"""

    authorize_url: str = Field(description="飞书授权页重定向 URL")


class ChangePasswordRequest(BaseModel):
    """修改密码请求。"""

    old_password: str = Field(min_length=6, max_length=128, description="当前密码")
    new_password: str = Field(min_length=6, max_length=128, description="新密码")


class UserInfoResponse(BaseModel):
    """当前用户信息响应。"""

    id: int
    username: str
    email: str
    display_name: str | None = None
    avatar_url: str | None = None
    auth_source: str
    is_active: bool
    is_superuser: bool
    roles: list[str] = Field(description="角色 code 列表")
    permissions: list[str] = Field(description="权限 code 列表")
