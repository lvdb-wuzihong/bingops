"""Role 和 Permission ORM 模型。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bingops.models.base import Base, BaseMixin

if TYPE_CHECKING:
    from bingops.models.user import User


class Permission(BaseMixin, Base):
    """权限表 ORM 模型。

    权限编码格式：{resource}:{action}，如 host:create、deploy:execute。
    """

    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class Role(BaseMixin, Base):
    """角色表 ORM 模型。

    系统内置角色（is_system=True）不可删除。
    """

    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # 关联
    users: Mapped[list[User]] = relationship(
        "User",
        secondary="user_roles",
        lazy="selectin",
        back_populates="roles",
    )
    permissions: Mapped[list[Permission]] = relationship(
        "Permission",
        secondary="role_permissions",
        lazy="selectin",
    )
