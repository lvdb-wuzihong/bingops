"""认证相关 API 路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.api.dependencies import get_current_user, get_db_session
from bingops.core.exceptions import AuthenticationError
from bingops.core.response import success_response
from bingops.core.security import MFA_SETUP_TICKET_TYPE, verify_token
from bingops.models.user import User
from bingops.repositories.user_repo import UserRepo
from bingops.schemas.auth import (
    ChangePasswordRequest,
    FeishuLoginResponse,
    LoginRequest,
    MfaDisableRequest,
    MfaEnableRequest,
    MfaSetupResponse,
    MfaVerifyRequest,
    RefreshRequest,
    UserInfoResponse,
)
from bingops.services import auth_service

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_bearer_optional = HTTPBearer(auto_error=False)


async def _resolve_mfa_binding_user(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_optional),
    session: AsyncSession = Depends(get_db_session),
) -> User:
    """MFA 绑定身份：登录态 access token 或强制绑定 setup_ticket（二选一）。"""
    if credentials is None:
        raise AuthenticationError("Missing authorization header")
    try:
        payload = verify_token(credentials.credentials)
    except JWTError:
        raise AuthenticationError("Invalid or expired token")
    if payload.get("type") not in ("access", MFA_SETUP_TICKET_TYPE):
        raise AuthenticationError("Invalid token type")
    try:
        user_id = int(payload.get("sub", ""))
    except (ValueError, TypeError):
        raise AuthenticationError("Invalid token payload")
    user = await UserRepo(session).get_by_id(user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("User not found or disabled")
    return user


@router.post("/login")
async def login(
    payload: LoginRequest,
    session: AsyncSession = Depends(get_db_session),
):
    """本地账号登录（MFA 策略下可能返回挑战响应而非 JWT）。"""
    result = await auth_service.login(session, payload.username, payload.password)
    return success_response(data=result.model_dump(mode="json"))


@router.post("/refresh")
async def refresh_token(
    payload: RefreshRequest,
    session: AsyncSession = Depends(get_db_session),
):
    """刷新 Token。"""
    token_resp = await auth_service.refresh(session, payload.refresh_token)
    return success_response(data=token_resp.model_dump(mode="json"))


@router.post("/logout")
async def logout(user: User = Depends(get_current_user)):
    """登出（客户端删除 Token 即可，此端点用于审计记录）。"""
    return success_response(message="Logged out successfully")


@router.get("/me")
async def get_me(
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    """获取当前用户信息 + 权限列表。"""
    role_codes = [r.code for r in user.roles]
    perm_codes: list[str] = []
    for role in user.roles:
        perm_codes.extend(p.code for p in role.permissions)
    perm_codes = list(set(perm_codes))

    data = UserInfoResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        auth_source=user.auth_source,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        has_password=user.password_hash is not None,
        mfa_enabled=user.mfa_enabled,
        roles=role_codes,
        permissions=perm_codes,
    )
    return success_response(data=data.model_dump(mode="json"))


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    """修改当前用户密码。"""
    await auth_service.change_password(session, user, payload.old_password, payload.new_password)
    return success_response(message="Password changed successfully")


@router.post("/login/mfa")
async def login_mfa(
    payload: MfaVerifyRequest,
    session: AsyncSession = Depends(get_db_session),
):
    """登录第二因素校验：mfa_ticket + 6 位动态码换正式 JWT。"""
    token_resp = await auth_service.login_mfa(session, payload.mfa_ticket, payload.otp_code)
    return success_response(data=token_resp.model_dump(mode="json"))


@router.post("/mfa/setup")
async def mfa_setup(
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(_resolve_mfa_binding_user),
):
    """初始化 MFA 绑定：返回 secret 与 otpauth URI（前端渲染二维码）。"""
    secret, uri = await auth_service.mfa_setup(session, user)
    data = MfaSetupResponse(secret=secret, otpauth_uri=uri)
    return success_response(data=data.model_dump(mode="json"))


@router.post("/mfa/enable")
async def mfa_enable(
    payload: MfaEnableRequest,
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(_resolve_mfa_binding_user),
):
    """校验一次动态码完成绑定；响应直接携带正式 JWT（强制绑定流程闭环）。"""
    token_resp = await auth_service.mfa_enable(session, user, payload.otp_code)
    return success_response(data=token_resp.model_dump(mode="json"))


@router.post("/mfa/disable")
async def mfa_disable(
    payload: MfaDisableRequest,
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    """解绑 MFA（密码 + 当前动态码双验证）。"""
    await auth_service.mfa_disable(session, user, payload.password, payload.otp_code)
    return success_response(message="MFA disabled successfully")


@router.get("/feishu/login")
async def feishu_login():
    """获取飞书授权页 URL。"""
    url = await auth_service.feishu_login_url()
    data = FeishuLoginResponse(authorize_url=url)
    return success_response(data=data.model_dump(mode="json"))


@router.get("/feishu/callback")
async def feishu_callback(
    code: str,
    session: AsyncSession = Depends(get_db_session),
):
    """飞书 SSO 回调：code 换 JWT。"""
    token_resp = await auth_service.feishu_callback(session, code)
    return success_response(data=token_resp.model_dump(mode="json"))
