"""认证提供者协议与本地账号实现。"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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


class LocalAuthProvider:
    """本地账号密码认证。

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
            logger.warning("Login failed: user not found", extra={"username": username})
            raise AuthenticationError("Invalid username or password")

        if not user.password_hash:
            logger.warning("Login failed: no password set", extra={"username": username})
            raise AuthenticationError("Invalid username or password")

        if not verify_password(password, user.password_hash):
            logger.warning("Login failed: wrong password", extra={"username": username})
            raise AuthenticationError("Invalid username or password")

        if not user.is_active:
            logger.warning("Login failed: user disabled", extra={"username": username})
            raise AuthenticationError("Account is disabled")

        return user
