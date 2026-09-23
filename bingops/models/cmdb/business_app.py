"""CMDB 业务应用 ORM 模型。"""

from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bingops.models.base import Base, BaseMixin


class CmdbBusinessDomain(BaseMixin, Base):
    """业务域：业务域 → 应用 → 资源 三层归属的业务分组层（应用之上的唯一分组，不再建模更高层）。"""

    __tablename__ = "cmdb_business_domains"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


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

    # 归属业务域（可空，存量渐进补挂）
    business_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("cmdb_business_domains.id"), nullable=True,
    )
    # 依赖声明：[{type: internal, app_code} / {type: external, name, url}]
    dependencies: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
