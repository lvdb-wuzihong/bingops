"""CMDB 业务应用 ORM 模型。"""

from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bingops.models.base import Base, BaseMixin


class CmdbBusinessApp(BaseMixin, Base):
    """业务应用表。

    通过 cmdb_resource_tags 中的 tag_key='app' 与资源关联，
    不使用外键直连。
    """

    __tablename__ = "cmdb_business_apps"

    app_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    team: Mapped[str | None] = mapped_column(String(128), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    department: Mapped[str | None] = mapped_column(String(128), nullable=True)

    labels: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # 研发资产坐标：仓库地址 + 各环境流水线地址（{环境: 地址}，key 对齐 env 标签值域）
    repo_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    pipelines: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
