"""CMDB 变更记录 ORM 模型。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from bingops.models.base import Base


class CmdbChangeLog(Base):
    """变更记录表（不可变日志，仅 created_at）。

    记录资源的全量操作审计，支持 create/update/delete/relate/unrelate/tag。
    """

    __tablename__ = "cmdb_change_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    resource_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    model_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # 变更类型：'create' | 'update' | 'delete' | 'relate' | 'unrelate' | 'tag'
    change_type: Mapped[str] = mapped_column(String(16), nullable=False)

    # 变更内容
    field: Mapped[str | None] = mapped_column(String(128), nullable=True)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 来源
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="discovery")
    operator: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
