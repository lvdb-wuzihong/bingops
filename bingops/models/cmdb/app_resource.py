"""CMDB 应用-资源关联 ORM 模型（附录 B #13 物化）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from bingops.models.base import Base


class CmdbAppResource(Base):
    """业务应用 ↔ 资源显式关联表。

    source='tag'：tag_key='app'（含 k8s:app 标签）自动归集；
    source='manual'：API 手动绑定。
    应用只绑服务级 CI（workload/中间件/RDS/入口），不绑 Pod/Node（service 层校验）。
    """

    __tablename__ = "cmdb_app_resources"
    __table_args__ = (
        UniqueConstraint("app_id", "resource_id", name="uq_cmdb_app_resource"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cmdb_business_apps.id", ondelete="CASCADE"), nullable=False,
    )
    resource_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cmdb_resources.id", ondelete="CASCADE"), nullable=False,
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="tag")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        nullable=False,
    )
