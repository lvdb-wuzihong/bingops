"""SQLAlchemy ORM 基类与公共 Mixin。

所有 ORM 模型必须通过 Mixin 继承公共字段，禁止在各模型中重复定义。

继承规则：
- 标准业务表（users, roles, hosts 等）→ 继承 BaseMixin
- 审计日志等不可变表（change_log）→ 仅使用 created_at
- 关联表（role_permissions, user_roles）→ 使用联合主键，不使用 BaseMixin
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """SQLAlchemy 声明基类。"""


class TimestampMixin:
    """时间戳字段 Mixin（created_at + updated_at）。

    适用于需要记录创建和更新时间的业务表。
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class BaseMixin(TimestampMixin):
    """自增主键 + 时间戳 Mixin。

    90% 的业务表继承此 Mixin，包含：
    - id: 自增主键
    - created_at: 创建时间（UTC）
    - updated_at: 更新时间（UTC，自动维护）

    使用示例::

        class User(BaseMixin, Base):
            __tablename__ = "users"
            username: Mapped[str] = mapped_column(String(64), unique=True)
    """

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
