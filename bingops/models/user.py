"""User ORM 模型。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bingops.models.base import Base, BaseMixin

if TYPE_CHECKING:
    from bingops.models.role import Role


class User(BaseMixin, Base):
    """用户表 ORM 模型。

    支持本地账号和飞书 SSO 两种认证来源。
    """

    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # 认证来源：'local' | 'feishu'
    auth_source: Mapped[str] = mapped_column(String(16), nullable=False, default="local")

    # 飞书 SSO 字段
    feishu_open_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    feishu_union_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # 状态
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 关联
    roles: Mapped[list[Role]] = relationship(
        "Role",
        secondary="user_roles",
        lazy="selectin",
        back_populates="users",
    )
