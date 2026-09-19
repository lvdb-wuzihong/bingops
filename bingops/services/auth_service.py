"""认证编排服务。

负责登录、Token 刷新、登出、飞书 SSO 流程。
"""

from __future__ import annotations

import logging

from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.core.auth_providers import LocalAuthProvider
from bingops.core.config import settings
from bingops.core.exceptions import AuthenticationError, ValidationError
from bingops.core.feishu_provider import FeishuAuthProvider
from bingops.core.security import (
    build_token_payload,
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
    verify_token,
)
from bingops.models.user import User
from bingops.repositories.role_repo import RoleRepo
from bingops.repositories.user_repo import UserRepo
from bingops.schemas.auth import TokenResponse

logger = logging.getLogger(f"bingops.{__name__}")

_local_provider = LocalAuthProvider()
_feishu_provider = FeishuAuthProvider()


async def login(session: AsyncSession, username: str, password: str) -> TokenResponse:
    """本地账号登录。"""
    user = await _local_provider.authenticate(session, {"username": username, "password": password})
    user_repo = UserRepo(session)
    await user_repo.update_last_login(user)
    await session.commit()
    return _build_token_response(user)


async def feishu_callback(session: AsyncSession, code: str) -> TokenResponse:
    """飞书 SSO 回调处理。"""
    user = await _feishu_provider.authenticate(session, {"code": code})
    user_repo = UserRepo(session)
    await user_repo.update_last_login(user)
    await session.commit()
    return _build_token_response(user)


async def feishu_login_url() -> str:
    """获取飞书授权页 URL。"""
    return await _feishu_provider.get_authorize_url()


async def refresh(session: AsyncSession, refresh_token: str) -> TokenResponse:
    """刷新 Token。"""
    try:
        payload = verify_token(refresh_token)
    except JWTError:
        raise AuthenticationError("Invalid or expired refresh token")

    if payload.get("type") != "refresh":
        raise AuthenticationError("Invalid token type")

    user_id_raw = payload.get("sub", "")
    try:
        user_id = int(user_id_raw)
    except (ValueError, TypeError):
        raise AuthenticationError("Invalid user ID in token")

    user_repo = UserRepo(session)
    user = await user_repo.get_by_id(user_id)

    if user is None or not user.is_active:
        raise AuthenticationError("User not found or disabled")

    await session.commit()
    return _build_token_response(user)


async def get_me(session: AsyncSession, user_id: int) -> User:
    """获取当前用户信息。"""
    user_repo = UserRepo(session)
    user = await user_repo.get_by_id(user_id)
    if user is None:
        raise AuthenticationError("User not found")
    return user


async def change_password(session: AsyncSession, user: User, old_password: str | None, new_password: str) -> None:
    """修改当前用户密码；无密码用户（飞书 SSO 开户）首次设置密码时免验旧密码。"""
    if user.password_hash:
        # 已有密码：必须验旧密码，防止登录态被盗后直接改密
        if not old_password:
            raise ValidationError("Old password is required")
        if not verify_password(old_password, user.password_hash):
            raise AuthenticationError("Old password is incorrect")
        if old_password == new_password:
            raise ValidationError("New password must be different from old password")

    is_first_set = user.password_hash is None
    user.password_hash = hash_password(new_password)
    user_repo = UserRepo(session)
    await user_repo.update(user)
    await session.commit()

    logger.info("User password changed", extra={"user_id": user.id, "first_time_set": is_first_set})


def _build_token_response(user: User) -> TokenResponse:
    """构建 Token 响应。"""
    role_codes = [r.code for r in user.roles]
    permission_codes: list[str] = []
    for role in user.roles:
        permission_codes.extend(p.code for p in role.permissions)
    permission_codes = list(set(permission_codes))

    extra = build_token_payload(
        user_id=str(user.id),
        roles=role_codes,
        permissions=permission_codes,
        is_superuser=user.is_superuser,
    )

    access = create_access_token(str(user.id), extra=extra)
    refresh = create_refresh_token(str(user.id))

    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_expire_minutes * 60,
    )
