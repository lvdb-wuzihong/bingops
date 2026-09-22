"""CMDB 资源收藏 ORM 模型（我的关注）。"""

from __future__ import annotations

from sqlalchemy import BigInteger, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from bingops.models.base import Base


class CmdbResourceFavorite(Base):
    """用户收藏的资源（个人视图标记，与资源生命周期解耦）。"""

    __tablename__ = "cmdb_resource_favorites"
    __table_args__ = (
        UniqueConstraint("user_id", "resource_id", name="uq_cmdb_resource_favorite"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    resource_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cmdb_resources.id", ondelete="CASCADE"), nullable=False,
    )
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
