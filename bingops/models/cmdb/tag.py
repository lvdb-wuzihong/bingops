"""CMDB 标签 ORM 模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bingops.models.base import Base, BaseMixin


class CmdbTagDefinition(BaseMixin, Base):
    """标签定义表（标签字典）。

    定义可用的标签 key、分类、值约束和可编辑性。
    """

    __tablename__ = "cmdb_tag_definitions"

    tag_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 分类：'system' | 'cloud' | 'custom'
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="custom")

    # 值类型：'string' | 'enum' | 'number' | 'boolean'
    value_type: Mapped[str] = mapped_column(String(16), nullable=False, default="string")

    # enum 时的可选值列表
    allowed_values: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # cloud 类标签 editable=false
    editable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class CmdbResourceTag(BaseMixin, Base):
    """资源标签关联表。

    标签的唯一数据源，支持多来源隔离（cloud / manual / rule）。
    """

    __tablename__ = "cmdb_resource_tags"
    __table_args__ = (
        UniqueConstraint("resource_id", "tag_key", "source", name="uq_cmdb_resource_tag"),
    )

    resource_id: Mapped[int] = mapped_column(
        nullable=False, index=True,
    )
    tag_key: Mapped[str] = mapped_column(String(128), nullable=False)
    tag_value: Mapped[str] = mapped_column(Text, nullable=False)

    # 来源：'cloud' | 'manual' | 'rule'
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")

    # 云厂商原始 key（归一化前）
    raw_key: Mapped[str | None] = mapped_column(String(256), nullable=True)

    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    operator: Mapped[str | None] = mapped_column(String(128), nullable=True)
