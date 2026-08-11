"""用户-角色 和 角色-权限 关联表 ORM 模型。"""

from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from bingops.models.base import Base


class UserRole(Base):
    """用户-角色关联表。

    联合主键 (user_id, role_id)，不使用 BaseMixin。
    """

    __tablename__ = "user_roles"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True,
    )
    role_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True,
    )


class RolePermission(Base):
    """角色-权限关联表。

    联合主键 (role_id, permission_id)，不使用 BaseMixin。
    """

    __tablename__ = "role_permissions"

    role_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True,
    )
    permission_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True,
    )
