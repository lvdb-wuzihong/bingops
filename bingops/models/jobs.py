"""任务系统 ORM 模型（P1：Ansible 执行引擎，设计见 docs/task-system-design.md）。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bingops.models.base import Base, BaseMixin


class Runbook(BaseMixin, Base):
    """Runbook（任务模板）。

    steps 为有序步骤 JSON 数组，步骤契约见 docs/task-system-design.md §3：
    key/name/type/playbook/timeout_sec/serial/batch_pause_sec/rollbackable。
    编辑 steps/params_schema/connection 时 version +1，execution 创建时快照。
    """

    __tablename__ = "runbooks"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    params_schema: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    steps: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # 连接配置：{ssh_user, ssh_key_ref}（钥匙名进消息，真钥匙在 Vault）
    connection: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # 目标模型范围（执行清单硬校验依据，P1 默认云主机两类）
    target_models: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=lambda: ["aliyun_ecs", "gcp_compute"],
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    risk_level: Mapped[str] = mapped_column(
        String(16), nullable=False, default="low",
    )  # low|medium|high|critical
    auto_rollback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )


class JobExecution(BaseMixin, Base):
    """任务执行实例（创建时三快照：runbook_version/steps/targets）。"""

    __tablename__ = "job_executions"

    runbook_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("runbooks.id"), nullable=False,
    )
    runbook_version: Mapped[int] = mapped_column(Integer, nullable=False)
    code_ref: Mapped[str] = mapped_column(String(128), nullable=False)  # git tag 快照
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # [{resource_id,name,ip,region,model_code}]
    target_resources: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    steps_snapshot: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # 连接配置快照 {ssh_user, ssh_key_ref}（回滚下发同样需要）
    connection: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending",
    )  # pending|running|success|failed|rolling_back|rolled_back|partial_rollback|cancelled
    rollback_policy: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manual",
    )  # manual|auto
    ticket_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # P3 审批挂接
    triggered_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JobStep(BaseMixin, Base):
    """步骤执行记录（回滚 = 同 step_key 的 attempt_type='rollback' 新行）。"""

    __tablename__ = "job_steps"
    __table_args__ = (
        UniqueConstraint(
            "execution_id", "step_key", "attempt_type", name="uq_job_step_key_attempt",
        ),
    )

    execution_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("job_executions.id", ondelete="CASCADE"), nullable=False,
    )
    step_key: Mapped[str] = mapped_column(String(64), nullable=False)
    step_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False, default="ansible")
    attempt_type: Mapped[str] = mapped_column(String(16), nullable=False, default="do")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending",
    )  # pending|running|success|failed|skipped|rolled_back|rollback_failed
    serial: Mapped[str | None] = mapped_column(String(16), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JobStepLog(Base):
    """步骤日志（不可变，仅 logged_at；90 天保留定期 purge）。"""

    __tablename__ = "job_step_logs"
    __table_args__ = (
        UniqueConstraint("step_id", "seq", name="uq_job_step_log_seq"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    step_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("job_steps.id", ondelete="CASCADE"), nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    host: Mapped[str | None] = mapped_column(String(128), nullable=True)
    line: Mapped[str] = mapped_column(Text, nullable=False)
    logged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
