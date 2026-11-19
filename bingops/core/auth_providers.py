"""认证提供者协议与本地账号实现。"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.core.config import settings
from bingops.core.exceptions import AuthenticationError
from bingops.core.security import verify_password
from bingops.models.user import User

logger = logging.getLogger(f"bingops.{__name__}")


@runtime_checkable
class AuthProvider(Protocol):
    """认证提供者协议。

    所有认证方式（本地、飞书、LDAP 等）必须实现此接口。
    """

    async def authenticate(self, session: AsyncSession, credentials: dict) -> User:
        """验证凭据并返回用户对象。

        Args:
            session: 数据库会话。
            credentials: 认证凭据（格式由各实现定义）。

        Returns:
            认证通过的 User 实例。

        Raises:
            AuthenticationError: 认证失败。
        """
        ...


async def record_login_failure(session: AsyncSession, user: User) -> None:
    """累计失败次数（窗口外重置，达阈值锁定）。账密登录与 MFA 校验共用。"""
    now = datetime.now(UTC)
    window = timedelta(minutes=settings.login_fail_window_minutes)
    if user.last_failed_login_at is None or now - user.last_failed_login_at > window:
        user.failed_login_count = 1
    else:
        user.failed_login_count = (user.failed_login_count or 0) + 1
    user.last_failed_login_at = now

    if user.failed_login_count >= settings.login_fail_max_attempts:
        user.locked_until = now + timedelta(minutes=settings.login_lock_minutes)
        logger.warning(
            "Account locked after too many failed logins",
            extra={
                "username": user.username,
                "attempts": user.failed_login_count,
                "locked_until": user.locked_until.isoformat(),
            },
        )
    await session.flush()


class LocalAuthProvider:
    """本地账号密码认证（含失败限流：窗口内失败达阈值即锁定）。

    credentials 格式: {"username": str, "password": str}
    """

    async def authenticate(self, session: AsyncSession, credentials: dict) -> User:
        username = credentials.get("username", "")
        password = credentials.get("password", "")

        result = await session.execute(
            select(User).where(User.username == username)
        )
        user = result.scalar_one_or_none()

        if user is None:
            # 无行可计数：不存在的用户名直接拒绝（防枚举：错误文案与密码错一致）
            logger.warning("Login failed: user not found", extra={"username": username})
            raise AuthenticationError("Invalid username or password")

        # 锁定检查在密码校验之前：锁定期不比对密码，也不累计计数（避免永久续锁）
        now = datetime.now(UTC)
        if user.locked_until is not None and user.locked_until > now:
            remaining = max(1, int((user.locked_until - now).total_seconds() // 60) + 1)
            logger.warning(
                "Login blocked: account locked",
                extra={"username": username, "locked_until": user.locked_until.isoformat()},
            )
            raise AuthenticationError(
                f"Account locked due to too many failed attempts, try again in {remaining} minutes"
            )

        if not user.password_hash:
            logger.warning("Login failed: no password set", extra={"username": username})
            raise AuthenticationError("Invalid username or password")

        if not verify_password(password, user.password_hash):
            await record_login_failure(session, user)
            logger.warning("Login failed: wrong password", extra={"username": username})
            raise AuthenticationError("Invalid username or password")

        if not user.is_active:
            logger.warning("Login failed: user disabled", extra={"username": username})
            raise AuthenticationError("Account is disabled")

        # 登录成功：清除失败计数与过期锁定标记
        if user.failed_login_count or user.last_failed_login_at or user.locked_until:
            user.failed_login_count = 0
            user.last_failed_login_at = None
            user.locked_until = None
            await session.flush()

        return user
