"""CMDB 动态模型 ORM 模型。

包含模型分类、模型定义、字段定义、模型关系、公共选项库。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bingops.models.base import Base, BaseMixin


class CmdbModelCategory(BaseMixin, Base):
    """模型分类表。

    模型分类是模型的容器，形成左侧导航树。
    """

    __tablename__ = "cmdb_model_categories"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 关联
    models: Mapped[list[CmdbModel]] = relationship(
        back_populates="category", cascade="all, delete-orphan", lazy="selectin",
    )


class CmdbModel(BaseMixin, Base):
    """模型定义表。

    每个模型代表一种资源类型（如 ECS 主机、K8s Pod）。
    """

    __tablename__ = "cmdb_models"

    category_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cmdb_model_categories.id"), nullable=False,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 关联
    category: Mapped[CmdbModelCategory] = relationship(back_populates="models", lazy="selectin")
    fields: Mapped[list[CmdbModelField]] = relationship(
        back_populates="model", cascade="all, delete-orphan", lazy="selectin",
        order_by="CmdbModelField.sort_order",
    )


class CmdbModelField(BaseMixin, Base):
    """字段定义表。

    每个模型可自定义字段，字段值存储在实例的 fields JSONB 中。
    """

    __tablename__ = "cmdb_model_fields"
    __table_args__ = (
        UniqueConstraint("model_id", "code", name="uq_cmdb_field_model_code"),
    )

    model_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cmdb_models.id", ondelete="CASCADE"), nullable=False,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    field_type: Mapped[str] = mapped_column(String(32), nullable=False)
    group_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_unique: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_searchable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    default_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    placeholder: Mapped[str | None] = mapped_column(String(256), nullable=True)
    options: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    option_set_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 关联
    model: Mapped[CmdbModel] = relationship(back_populates="fields", lazy="selectin")


class CmdbModelRelation(Base):
    """模型关系定义表。

    定义模型之间允许建立的关系约束。
    """

    __tablename__ = "cmdb_model_relations"
    __table_args__ = (
        UniqueConstraint(
            "source_model_id", "target_model_id", "relation_type",
            name="uq_cmdb_model_relation",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_model_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cmdb_models.id", ondelete="CASCADE"), nullable=False,
    )
    target_model_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cmdb_models.id", ondelete="CASCADE"), nullable=False,
    )
    relation_type: Mapped[str] = mapped_column(String(16), nullable=False)
    relation_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
    )


class CmdbOptionSet(BaseMixin, Base):
    """公共选项库。

    跨模型复用的枚举值集合。
    """

    __tablename__ = "cmdb_option_sets"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    options: Mapped[dict] = mapped_column(JSONB, nullable=False)
