"""认证编排服务。

负责登录、Token 刷新、登出、飞书 SSO 流程。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from cryptography.fernet import InvalidToken
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.core.auth_providers import LocalAuthProvider, record_login_failure
from bingops.core.config import settings
from bingops.core.exceptions import AuthenticationError, ValidationError
from bingops.core.feishu_provider import FeishuAuthProvider
from bingops.core.security import (
    MFA_SETUP_TICKET_TYPE,
    MFA_TICKET_TYPE,
    build_token_payload,
    build_totp_uri,
    create_access_token,
    create_mfa_ticket,
    create_refresh_token,
    decrypt_secret,
    encrypt_secret,
    generate_totp_secret,
    hash_password,
    verify_mfa_ticket,
    verify_password,
    verify_token,
    verify_totp,
)
from bingops.models.user import User
from bingops.repositories.user_repo import UserRepo
from bingops.schemas.auth import MfaChallengeResponse, TokenResponse

logger = logging.getLogger(f"bingops.{__name__}")

_local_provider = LocalAuthProvider()
_feishu_provider = FeishuAuthProvider()


async def login(
    session: AsyncSession, username: str, password: str,
) -> TokenResponse | MfaChallengeResponse:
    """本地账号登录（双段式）：密码通过后按 MFA 状态决定直接发 JWT 或发短期票据。"""
    try:
        user = await _local_provider.authenticate(
            session, {"username": username, "password": password},
        )
    except AuthenticationError:
        # 持久化失败计数/锁定状态（provider 内已 flush）；无变更时 commit 为空操作
        await session.commit()
        raise

    if user.mfa_enabled:
        ticket = create_mfa_ticket(str(user.id), purpose=MFA_TICKET_TYPE)
        logger.info("Login password verified, MFA required", extra={"user_id": user.id})
        return MfaChallengeResponse(mfa_required=True, mfa_ticket=ticket)

    if _mfa_required_by_policy(user):
        ticket = create_mfa_ticket(str(user.id), purpose=MFA_SETUP_TICKET_TYPE)
        logger.info(
            "Login password verified, MFA binding required by policy",
            extra={"user_id": user.id},
        )
        return MfaChallengeResponse(mfa_setup_required=True, setup_ticket=ticket)

    return await _complete_login(session, user)


async def login_mfa(session: AsyncSession, mfa_ticket: str, otp_code: str) -> TokenResponse:
    """登录第二因素校验：mfa_ticket + 动态码换正式 JWT。"""
    user = await _user_from_ticket(session, mfa_ticket, purpose=MFA_TICKET_TYPE)
    if not user.mfa_enabled:
        raise ValidationError("MFA is not enabled for this account")
    secret = _user_totp_secret(user)
    if not verify_totp(secret, otp_code):
        await record_login_failure(session, user)
        await session.commit()
        logger.warning("MFA verification failed", extra={"user_id": user.id})
        raise AuthenticationError("Invalid verification code")
    return await _complete_login(session, user)


def _mfa_required_by_policy(user: User) -> bool:
    """强制策略：off | admins_only（超管或 admin 角色）| all；仅用于本地账密登录。"""
    policy = settings.mfa_required.strip().lower()
    if policy == "all":
        return True
    if policy == "admins_only":
        return user.is_superuser or any(role.code == "admin" for role in user.roles)
    return False


async def _user_from_ticket(session: AsyncSession, ticket: str, *, purpose: str) -> User:
    """校验 MFA 短期票据并加载用户。"""
    try:
        user_id = verify_mfa_ticket(ticket, purpose=purpose)
    except JWTError:
        raise AuthenticationError("Invalid or expired MFA ticket")
    except (ValueError, TypeError):
        raise AuthenticationError("Invalid MFA ticket payload")
    user = await UserRepo(session).get_by_id(user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("User not found or disabled")
    if user.locked_until is not None and user.locked_until > datetime.now(UTC):
        raise AuthenticationError("Account locked due to too many failed attempts")
    return user


def _user_totp_secret(user: User) -> str:
    """解密用户 TOTP secret；密钥轮换/数据损坏时提示重新绑定。"""
    if not user.totp_secret:
        raise ValidationError("MFA is not configured")
    try:
        return decrypt_secret(user.totp_secret)
    except InvalidToken:
        logger.error("MFA secret decrypt failed", extra={"user_id": user.id})
        raise ValidationError("MFA secret is invalid, contact administrator to rebind")


async def _complete_login(session: AsyncSession, user: User) -> TokenResponse:
    """登录完成：更新最后登录时间并签发 JWT。"""
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


async def change_password(
    session: AsyncSession, user: User, old_password: str | None, new_password: str,
) -> None:
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


async def mfa_setup(session: AsyncSession, user: User) -> tuple[str, str]:
    """初始化 MFA 绑定：生成并加密保存 secret，返回 (secret, otpauth_uri)。

    此时 mfa_enabled 仍为 False，需调用 mfa_enable 校验动态码后生效。
    """
    if user.mfa_enabled:
        raise ValidationError("MFA is already enabled; disable it before rebinding")
    secret = generate_totp_secret()
    user.totp_secret = encrypt_secret(secret)
    username = user.username  # commit 前取值，不依赖 expire_on_commit 配置
    await session.flush()
    await session.commit()
    logger.info("MFA binding initialized", extra={"user_id": user.id})
    return secret, build_totp_uri(secret, username)


async def mfa_enable(session: AsyncSession, user: User, otp_code: str) -> TokenResponse:
    """校验一次动态码完成绑定；返回正式 JWT（强制绑定流程在此闭环）。"""
    if user.mfa_enabled:
        raise ValidationError("MFA is already enabled")
    secret = _user_totp_secret(user)
    if not verify_totp(secret, otp_code):
        await record_login_failure(session, user)
        await session.commit()
        raise AuthenticationError("Invalid verification code")
    user.mfa_enabled = True
    user.mfa_bound_at = datetime.now(UTC)
    await session.flush()
    logger.info("MFA enabled", extra={"user_id": user.id})
    return await _complete_login(session, user)


async def mfa_disable(session: AsyncSession, user: User, password: str, otp_code: str) -> None:
    """解绑 MFA：密码 + 当前动态码双验证。"""
    if not user.mfa_enabled:
        raise ValidationError("MFA is not enabled")
    if not user.password_hash or not verify_password(password, user.password_hash):
        raise AuthenticationError("Password is incorrect")
    secret = _user_totp_secret(user)
    if not verify_totp(secret, otp_code):
        await record_login_failure(session, user)
        await session.commit()
        raise AuthenticationError("Invalid verification code")
    user.mfa_enabled = False
    user.totp_secret = None
    user.mfa_bound_at = None
    await session.flush()
    await session.commit()
    logger.info("MFA disabled", extra={"user_id": user.id})


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
