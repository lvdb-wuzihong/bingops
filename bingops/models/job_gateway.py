"""中转网关（bastion）ORM 模型。

网关是**网络拓扑事实**：哪台机器要经谁中转，由机器所属 VPC 决定（VPC 之间
默认不通，同一 VPC 内的机器走同一个跳板）。所以关联维度**只有 VPC 一个**：
v32 曾做成四维（vpc/账号/区域/资源 ID）+ priority 消歧，那是“维度未拍板
就把选择权外包给表单”——四个框里多数永远不填，且多网关争同一批机器时需要人
记规则。v35 收敛为单一维度，一台机器的中转路径由它的 vpc_id 唯一确定。
设计见 docs/task-system-design.md §5.2。
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


class JobGateway(BaseMixin, Base):
    """中转网关条目：runner 渲染 ProxyCommand 的数据来源。"""

    __tablename__ = "job_gateways"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    host: Mapped[str] = mapped_column(String(128), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=22)
    login_user: Mapped[str] = mapped_column(String(64), nullable=False, default="root")
    # 网关自身的登录钥匙：引用 credentials.name（不是裸 Vault 路径，
    # 这样轮换时可反查到“哪些网关还在用这把钥匙”）
    ssh_credential: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 本网关负责哪些 VPC（与主机 fields.vpc_id 比对）；空 = 不接管任何机器。
    # 同一 VPC 不允许被两个启用网关声明（写入校验）：一个 VPC 多跳板是冷备
    # 场景，runner 未实现故障转移，配了也不会用，不如在写入时就拒
    vpc_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
