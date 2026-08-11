"""FastAPI 依赖注入工具。

提供 get_current_user 和 require_permission 等依赖函数。
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.core.exceptions import AuthenticationError, PermissionDeniedError
from bingops.core.security import verify_token
from bingops.models.user import User
from bingops.repositories.user_repo import UserRepo

_security = HTTPBearer(auto_error=False)


async def get_db_session() -> AsyncSession:
    """获取数据库会话（占位，实际需注入 sessionmaker）。

    在 main.py 中需要替换为实际的 session 注入：
        app.dependency_overrides[get_db_session] = get_async_session
    """
    raise NotImplementedError("Database session dependency not configured")


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Security(_security),
    session: AsyncSession = Depends(get_db_session),
) -> User:
    """解析 JWT 并返回当前用户。

    从 Authorization header 中提取 Bearer token，验证后查询用户。

    Raises:
        AuthenticationError: Token 无效、过期或用户不存在。
    """
    if credentials is None:
        raise AuthenticationError("Missing authorization header")

    token = credentials.credentials

    try:
        payload = verify_token(token)
    except JWTError:
        raise AuthenticationError("Invalid or expired token")

    if payload.get("type") != "access":
        raise AuthenticationError("Invalid token type")

    user_id_raw: str = payload.get("sub", "")
    if not user_id_raw:
        raise AuthenticationError("Invalid token payload")

    try:
        user_id = int(user_id_raw)
    except (ValueError, TypeError):
        raise AuthenticationError("Invalid user ID in token")

    repo = UserRepo(session)
    user = await repo.get_by_id(user_id)
    if user is None:
        raise AuthenticationError("User not found")
    if not user.is_active:
        raise AuthenticationError("Account is disabled")

    return user


def require_permission(*permissions: str):
    """要求当前用户拥有指定权限。

    用法::

        @router.post("/hosts")
        async def create_host(user: User = require_permission("host:create")):
            ...

    Raises:
        PermissionDeniedError: 用户缺少必要权限。
    """

    async def checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.is_superuser:
            return current_user

        user_perms: set[str] = set()
        for role in current_user.roles:
            for perm in role.permissions:
                user_perms.add(perm.code)

        for perm in permissions:
            if perm not in user_perms:
                raise PermissionDeniedError(f"Missing permission: {perm}")

        return current_user

    return Depends(checker)
