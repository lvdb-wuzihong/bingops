"""CMDB 资源实例 ORM 模型（v2 动态模型）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bingops.models.base import Base, BaseMixin


class CmdbResource(BaseMixin, Base):
    """CMDB 资源实例表。

    所有模型共用一张表，通过 model_id 关联模型定义，
    通过 fields (JSONB) 存储模型定义的动态字段值。
    """

    __tablename__ = "cmdb_resources"
    __table_args__ = (
        UniqueConstraint(
            "model_id", "provider", "provider_id", "cloud_account",
            name="uq_cmdb_resource_provider_id",
        ),
    )

    # 模型关联
    model_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cmdb_models.id"), nullable=False,
    )

    # 通用字段（所有资源共有）
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    cloud_account: Mapped[str | None] = mapped_column(String(128), nullable=True)
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    zone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 资源状态；NULL = 该资源类型无生命周期状态（采集端不硬塞），unknown = 有状态概念但识别失败
    status: Mapped[str | None] = mapped_column(String(32), nullable=True, default="unknown")

    # 动态字段（按模型定义填充）
    fields: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # 同步元数据
    resource_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")

    # 软删除
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
