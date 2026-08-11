"""角色与权限数据访问层。"""

from __future__ import annotations

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.models.role import Permission, Role
from bingops.models.user_role import RolePermission


class RoleRepo:
    """角色 Repository，封装所有角色/权限相关数据库操作。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── 角色 CRUD ─────────────────────────────────────────────────────────────

    async def list_roles(self) -> list[Role]:
        result = await self._session.execute(select(Role).order_by(Role.created_at))
        return list(result.scalars().all())

    async def get_by_id(self, role_id: str) -> Role | None:
        result = await self._session.execute(select(Role).where(Role.id == role_id))
        return result.scalar_one_or_none()

    async def get_by_code(self, code: str) -> Role | None:
        result = await self._session.execute(select(Role).where(Role.code == code))
        return result.scalar_one_or_none()

    async def create(self, role: Role) -> Role:
        self._session.add(role)
        await self._session.flush()
        return role

    async def update(self, role: Role) -> Role:
        await self._session.flush()
        return role

    async def delete(self, role_id: str) -> None:
        await self._session.execute(delete(Role).where(Role.id == role_id))

    # ── 权限管理 ──────────────────────────────────────────────────────────────

    async def list_permissions(self) -> list[Permission]:
        result = await self._session.execute(select(Permission).order_by(Permission.code))
        return list(result.scalars().all())

    async def get_permission_by_code(self, code: str) -> Permission | None:
        result = await self._session.execute(select(Permission).where(Permission.code == code))
        return result.scalar_one_or_none()

    async def get_permissions_by_codes(self, codes: list[str]) -> list[Permission]:
        if not codes:
            return []
        result = await self._session.execute(select(Permission).where(Permission.code.in_(codes)))
        return list(result.scalars().all())

    async def replace_permissions(self, role_id: str, permission_ids: list[str]) -> None:
        """全量替换角色的权限。"""
        await self._session.execute(delete(RolePermission).where(RolePermission.role_id == role_id))
        for perm_id in permission_ids:
            self._session.add(RolePermission(role_id=role_id, permission_id=perm_id))
        await self._session.flush()

    # ── 用户权限查询 ──────────────────────────────────────────────────────────

    async def get_user_permissions(self, user_id: str) -> list[str]:
        """获取用户的所有权限 code（通过角色聚合去重）。"""
        from sqlalchemy import func
        from bingops.models.user_role import UserRole

        stmt = (
            select(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(UserRole, UserRole.role_id == RolePermission.role_id)
            .where(UserRole.user_id == user_id)
            .distinct()
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
