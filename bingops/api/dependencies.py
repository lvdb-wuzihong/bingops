"""FastAPI 依赖注入工具。

提供 get_current_user 和 require_permission 等依赖函数。
"""

from __future__ import annotations

from fastapi import Depends, Request, Security
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


def has_permissions(user: User, *permissions: str) -> bool:
    """判断用户是否拥有全部指定权限码（超管恒真）。"""
    if user.is_superuser:
        return True
    perms = {p.code for role in user.roles for p in role.permissions}
    return all(p in perms for p in permissions)


# 工单利害关系人（创建人/处理人）免角色权限的动作白名单；
# approve/assign/delete 仍为角色专属，不开放免底
TICKET_STAKEHOLDER_ACTIONS = {"ticket:list", "ticket:update", "ticket:create"}


def require_ticket_permission(*permissions: str):
    """工单端点权限：角色权限优先，不足时对工单利害关系人（创建人/处理人）免底。

    免底仅开放 TICKET_STAKEHOLDER_ACTIONS 内的动作，
    使值班被派单人无需宽泛角色也能处理自己的工单。
    """

    async def checker(
        request: Request,
        current_user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db_session),
    ) -> User:
        if has_permissions(current_user, *permissions):
            return current_user

        if set(permissions) <= TICKET_STAKEHOLDER_ACTIONS:
            raw_id = request.path_params.get("ticket_id")
            try:
                ticket_id = int(raw_id)
            except (TypeError, ValueError):
                ticket_id = None
            if ticket_id is not None:
                from bingops.models.ticket import Ticket

                ticket = await session.get(Ticket, ticket_id)
                if ticket is not None and current_user.id in (
                    ticket.assignee_id, ticket.creator_id,
                ):
                    return current_user

        raise PermissionDeniedError(f"Missing permission: {permissions[0]}")

    return Depends(checker)


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
