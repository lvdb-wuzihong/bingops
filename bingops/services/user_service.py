"""用户管理服务。"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from bingops.core.exceptions import ConflictError, NotFoundError
from bingops.core.security import hash_password
from bingops.models.user import User
from bingops.repositories.role_repo import RoleRepo
from bingops.repositories.user_repo import UserRepo
from bingops.schemas.user import UserCreate, UserUpdate

logger = logging.getLogger(f"bingops.{__name__}")


async def list_users(
    session: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
) -> tuple[list[User], int]:
    """分页查询用户列表。"""
    repo = UserRepo(session)
    return await repo.list_users(page=page, page_size=page_size, keyword=keyword)


async def get_user(session: AsyncSession, user_id: int) -> User:
    """获取用户详情。"""
    repo = UserRepo(session)
    user = await repo.get_by_id(user_id)
    if user is None:
        raise NotFoundError("User", user_id)
    return user


async def create_user(session: AsyncSession, payload: UserCreate) -> User:
    """创建用户。"""
    repo = UserRepo(session)

    if await repo.get_by_username(payload.username):
        raise ConflictError("User", f"username '{payload.username}' already exists")
    if await repo.get_by_email(payload.email):
        raise ConflictError("User", f"email '{payload.email}' already exists")

    user = User(
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
        is_superuser=payload.is_superuser,
    )
    user = await repo.create(user)
    await session.commit()

    logger.info("User created", extra={"user_id": user.id, "username": user.username})
    return user


async def update_user(session: AsyncSession, user_id: int, payload: UserUpdate) -> User:
    """更新用户信息。"""
    repo = UserRepo(session)
    user = await repo.get_by_id(user_id)
    if user is None:
        raise NotFoundError("User", user_id)

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(user, field, value)

    user = await repo.update(user)
    await session.commit()

    logger.info("User updated", extra={"user_id": user_id})
    return user


async def delete_user(session: AsyncSession, user_id: int) -> None:
    """删除用户。"""
    repo = UserRepo(session)
    user = await repo.get_by_id(user_id)
    if user is None:
        raise NotFoundError("User", user_id)

    await repo.delete(user_id)
    await session.commit()
    logger.info("User deleted", extra={"user_id": user_id})


async def assign_roles(session: AsyncSession, user_id: int, role_codes: list[str]) -> User:
    """为用户分配角色（全量替换）。"""
    user_repo = UserRepo(session)
    role_repo = RoleRepo(session)

    user = await user_repo.get_by_id(user_id)
    if user is None:
        raise NotFoundError("User", user_id)

    role_ids: list[str] = []
    for code in role_codes:
        role = await role_repo.get_by_code(code)
        if role is None:
            raise NotFoundError("Role", code)
        role_ids.append(role.id)

    await user_repo.replace_roles(user_id, role_ids)
    await session.commit()

    # 重新加载用户数据
    user = await user_repo.get_by_id(user_id)
    logger.info("User roles assigned", extra={"user_id": user_id, "roles": role_codes})
    return user  # type: ignore[return-value]
