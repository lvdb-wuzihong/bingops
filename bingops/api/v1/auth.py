"""认证相关 API 路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.api.dependencies import get_current_user, get_db_session
from bingops.core.response import success_response
from bingops.models.user import User
from bingops.schemas.auth import (
    ChangePasswordRequest,
    FeishuLoginResponse,
    LoginRequest,
    RefreshRequest,
    TokenResponse,
    UserInfoResponse,
)
from bingops.services import auth_service

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login")
async def login(
    payload: LoginRequest,
    session: AsyncSession = Depends(get_db_session),
):
    """本地账号登录。"""
    token_resp = await auth_service.login(session, payload.username, payload.password)
    return success_response(data=token_resp.model_dump(mode="json"))


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
