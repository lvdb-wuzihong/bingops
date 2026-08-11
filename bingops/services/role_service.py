"""角色管理服务。"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from bingops.core.exceptions import ConflictError, NotFoundError, ValidationError
from bingops.models.role import Permission, Role
from bingops.repositories.role_repo import RoleRepo
from bingops.schemas.role import RoleCreate, RoleUpdate

logger = logging.getLogger(f"bingops.{__name__}")


async def list_roles(session: AsyncSession) -> list[Role]:
    """查询所有角色。"""
    repo = RoleRepo(session)
    return await repo.list_roles()


async def get_role(session: AsyncSession, role_id: int) -> Role:
    """获取角色详情。"""
    repo = RoleRepo(session)
    role = await repo.get_by_id(role_id)
    if role is None:
        raise NotFoundError("Role", role_id)
    return role


async def create_role(session: AsyncSession, payload: RoleCreate) -> Role:
    """创建角色。"""
    repo = RoleRepo(session)

    if await repo.get_by_code(payload.code):
        raise ConflictError("Role", f"code '{payload.code}' already exists")

    role = Role(
        code=payload.code,
        name=payload.name,
        description=payload.description,
    )
    role = await repo.create(role)
    await session.commit()

    logger.info("Role created", extra={"role_id": role.id, "code": role.code})
    return role


async def update_role(session: AsyncSession, role_id: int, payload: RoleUpdate) -> Role:
    """更新角色信息。"""
    repo = RoleRepo(session)
    role = await repo.get_by_id(role_id)
    if role is None:
        raise NotFoundError("Role", role_id)

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(role, field, value)

    role = await repo.update(role)
    await session.commit()

    logger.info("Role updated", extra={"role_id": role_id})
    return role


async def delete_role(session: AsyncSession, role_id: int) -> None:
    """删除角色（系统角色不可删）。"""
    repo = RoleRepo(session)
    role = await repo.get_by_id(role_id)
    if role is None:
        raise NotFoundError("Role", role_id)
    if role.is_system:
        raise ValidationError("System roles cannot be deleted")

    await repo.delete(role_id)
    await session.commit()
    logger.info("Role deleted", extra={"role_id": role_id})


async def assign_permissions(session: AsyncSession, role_id: int, permission_codes: list[str]) -> Role:
    """为角色分配权限（全量替换）。"""
    repo = RoleRepo(session)
    role = await repo.get_by_id(role_id)
    if role is None:
        raise NotFoundError("Role", role_id)

    permissions = await repo.get_permissions_by_codes(permission_codes)
    found_codes = {p.code for p in permissions}
    missing = set(permission_codes) - found_codes
    if missing:
        raise NotFoundError("Permission", ", ".join(missing))

    perm_ids = [p.id for p in permissions]
    await repo.replace_permissions(role_id, perm_ids)
    await session.commit()

    # 重新加载角色
    role = await repo.get_by_id(role_id)
    logger.info("Role permissions assigned", extra={"role_id": role_id, "permissions": permission_codes})
    return role  # type: ignore[return-value]


async def list_permissions(session: AsyncSession) -> list[Permission]:
    """查询所有权限。"""
    repo = RoleRepo(session)
    return await repo.list_permissions()
