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

    old_password: str | None = Field(
        default=None,
        min_length=6,
        max_length=128,
        description="当前密码；无密码用户（飞书 SSO 开户）首次设置密码时可不传",
    )
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
    has_password: bool = Field(description="是否已设置本地密码；False 时修改密码免验旧密码（首次设置）")
    mfa_enabled: bool = Field(description="是否已绑定 MFA（TOTP）")
    roles: list[str] = Field(description="角色 code 列表")
    permissions: list[str] = Field(description="权限 code 列表")


class MfaChallengeResponse(BaseModel):
    """MFA 挑战响应（密码校验通过但未直接发放 JWT）。"""

    mfa_required: bool = False
    mfa_setup_required: bool = False
    mfa_ticket: str | None = Field(default=None, description="已绑定用户：5 分钟 MFA 校验票据")
    setup_ticket: str | None = Field(default=None, description="强制绑定用户：5 分钟绑定票据（仅可访问绑定端点）")


class MfaVerifyRequest(BaseModel):
    """登录第二因素校验请求。"""

    mfa_ticket: str
    otp_code: str = Field(min_length=6, max_length=8)


class MfaSetupResponse(BaseModel):
    """MFA 绑定初始化响应（前端渲染二维码）。"""

    secret: str
    otpauth_uri: str


class MfaEnableRequest(BaseModel):
    """完成 MFA 绑定请求。"""

    otp_code: str = Field(min_length=6, max_length=8)


class MfaDisableRequest(BaseModel):
    """解绑 MFA 请求（密码 + 当前 TOTP 双验证）。"""

    password: str = Field(min_length=6, max_length=128)
    otp_code: str = Field(min_length=6, max_length=8)
