"""中转网关（bastion）ORM 模型。

网关是**网络拓扑事实**，不是任务属性：哪台机器能直达、哪台要经谁中转，
由机器的网段/云账号归属决定，不该让每个 runbook 重复声明（漏勾就表现为
SSH 超时，排查成本极高）。设计见 docs/task-system-design.md §5.2。
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bingops.models.base import Base, BaseMixin

# scope 支持的选择维度：命中任一即认为该网关可服务这台机器。
# 刻意不做"空 scope = 全局兜底"——那会让一个误配的网关接管全部流量。
GATEWAY_SCOPE_KEYS = ("vpc_ids", "cloud_accounts", "regions", "resource_ids")


class JobGateway(BaseMixin, Base):
    """中转网关条目：runner 渲染 ProxyCommand 的数据来源。"""

    __tablename__ = "job_gateways"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    host: Mapped[str] = mapped_column(String(128), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=22)
    login_user: Mapped[str] = mapped_column(String(64), nullable=False, default="root")
    # 网关自身的登录钥匙：引用 credentials.name（不是裸 Vault 路径，
    # 这样轮换时可反查到"哪些网关还在用这把钥匙"）
    ssh_credential: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 选择维度：{vpc_ids:[], cloud_accounts:[], regions:[], resource_ids:[]}
    scope: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # 多网关命中时按 priority 升序取第一个（确定性，不靠排序运气）
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
