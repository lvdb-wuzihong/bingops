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

    steps 列已于 v29 删除：**一个 runbook = 一个扁平步骤**（exec_type / entry / run_on /
    timeout_sec / rollbackable 直接成列），不再有 JSONB 步骤数组；契约见
    docs/task-system-design.md §3.5。v30 进一步删掉 undo_command / serial /
    batch_pause_sec 三个细粒度字段（回滚统一约定，并发度下沉到执行机配置）。
    编辑步骤列/params_schema/secrets_schema/connection/target_models 时 version +1，
    execution 创建时快照为单个 step_snapshot 对象。

    v26/v27 糖字段（ssh_* / become*）由 job_service 归一进 connection JSONB。
    """

    __tablename__ = "runbooks"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    params_schema: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # params_schema 条目 spec：type(string|number|boolean)/required/default/enum/description；
    # 前端按此渲染动态表单，下发校验时后端自动回填 default
    # 凭据三层分离（v27）：params 明文 / secrets_schema 走 Vault / connection.ssh_key_ref 目标机私钥
    secrets_schema: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # secrets_schema 条目 spec：{required, description, default_ref}；键名即注入的大写环境变量名，
    # 值一律为 Vault 钥匙名（路径#字段），红线：不得存明文
    # ── 唯一步骤（v29 扁平化：一个 runbook = 一个步骤）──
    # 执行类型：ansible | shell | python | terraform
    exec_type: Mapped[str] = mapped_column(String(16), nullable=False, default="ansible")
    # 执行入口：ansible=playbook 路径、python=脚本入口、terraform=工作目录；
    # shell 恒为命令字符串（跑仓库脚本就写 bash scripts/x.sh）
    entry: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 执行位置：target = SSH 到目标机；local = runner 本机
    run_on: Mapped[str] = mapped_column(String(16), nullable=False, default="target")
    timeout_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=600)
    # 不可逆任务显式写 false（默认 true：入口实现了 undo 分支即可回滚）
    rollbackable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # v30 删除 undo_command / serial / batch_pause_sec：回滚统一走 BINGOPS_ACTION=undo
    # 约定，多目标并发度属于执行机部署级配置，不再是任务属性
    # 连接配置：{ssh_user, ssh_key_ref, become, become_method, become_user}
    # 钥匙名进消息，真钥匙在 Vault；sudo 密码不进配置（NOPASSWD sudoers 纪律）
    # v27：仅当存在 run_on=target 步骤时才必需（无主机任务不再被硬卡）
    connection: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # 目标模型范围（执行清单硬校验依据，P1 默认云主机两类）
    target_models: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=lambda: ["aliyun_ecs", "gcp_compute"],
    )
    # v36 已删除 default_target_resource_ids / default_code_ref：目标机与代码版本
    # 是每次执行的核心决策，缓存在模板上会把“必须确认的一步”变成预选项，
    # 且 CMDB 自增 ID 与 git tag 都会腐烂。“复用上次的”由前端读执行历史实现
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    risk_level: Mapped[str] = mapped_column(
        String(16), nullable=False, default="low",
    )  # low|medium|high|critical
    # auto_rollback 已于 v28 删除（回滚一律手动）；执行层策略见 job_executions.rollback_policy
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
    # 执行期密钥引用快照 {变量名: Vault 钥匙名}（v27）；runner 现场解析为同名大写 env 并加入 redact
    secrets: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # [{resource_id,name,ip,region,model_code}]
    target_resources: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # 创建时快照的唯一步骤对象（v29：与 runbooks 步骤列同构，含 key/name）
    step_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # 连接配置快照 {ssh_user, ssh_key_ref}（回滚下发同样需要）
    connection: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending",
    )
    # pending|running|success|failed|rolling_back|rolled_back|
    # partial_rollback|rollback_failed|cancelled
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
