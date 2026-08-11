"""CMDB 资源关系 ORM 模型（v2）。

从属关系（belongs_to）和关联关系（relates_to）。
关系语义通过 description 表达，不再硬编码 relation_type。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bingops.models.base import Base


class CmdbBelongsTo(Base):
    """从属关系表（树形结构，child → parent）。"""

    __tablename__ = "cmdb_belongs_to"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    child_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cmdb_resources.id", ondelete="CASCADE"), nullable=False,
    )
    parent_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cmdb_resources.id", ondelete="CASCADE"), nullable=False,
    )
    description: Mapped[str | None] = mapped_column(String(256), nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="discovery")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        nullable=False,
    )


class CmdbRelatesTo(Base):
    """关联关系表（图结构，source ↔ target）。"""

    __tablename__ = "cmdb_relates_to"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cmdb_resources.id", ondelete="CASCADE"), nullable=False,
    )
    target_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cmdb_resources.id", ondelete="CASCADE"), nullable=False,
    )
    description: Mapped[str | None] = mapped_column(String(256), nullable=True)
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="discovery")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        nullable=False,
    )
