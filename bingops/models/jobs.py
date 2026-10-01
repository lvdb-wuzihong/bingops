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
    timeout_sec 直接成列），不再有 JSONB 步骤数组；契约见
    docs/task-system-design.md §3.5。v30 删掉 undo_command / serial /
    batch_pause_sec；**v37 删掉 rollbackable——回滚能力整体下线**（runner 从未实现，
    契约里留着只会让人以为“失败可以一键撤销”）。
    编辑步骤列/params_schema/secrets_schema/connection/target_models 时 version +1，
    execution 创建时快照为单个 step_snapshot 对象。
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
    # 执行类型：ansible | shell | script | python | terraform
    exec_type: Mapped[str] = mapped_column(String(16), nullable=False, default="ansible")
    # 执行入口：ansible=playbook 路径、script=仓库内脚本文件（推送执行）、
    # python=脚本入口、terraform=工作目录；shell 恒为内联命令（在目标机 shell 里跑）
    entry: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 执行位置：target = SSH 到目标机；local = runner 本机
    run_on: Mapped[str] = mapped_column(String(16), nullable=False, default="target")
    timeout_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=600)
    # v37 删除 rollbackable：平台不提供回滚。失败就是失败，由人根据日志修复；
    # 入口脚本自己保留 undo 分支也无害，但平台不再注入 BINGOPS_ACTION=undo 去触发
    # 连接配置：v34 起创建面不再写入，仅存存量兜底与 become* 提权开关
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
    # auto_rollback / rollback_policy 均已删除（v28/v37）：平台不做回滚，也无自动/手动之分
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
    # pending|running|success|failed|cancelled（v37：rolling_back / rolled_back /
    # partial_rollback / rollback_failed 四个回滚态随能力一并下线）
    # v37 删除 rollback_policy：不存在回滚，就无“自动/手动”之分
    ticket_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # P3 审批挂接
    triggered_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JobStep(BaseMixin, Base):
    """步骤执行记录（v37 起一步一行，不再有 do/rollback 两次尝试）。"""

    __tablename__ = "job_steps"
    __table_args__ = (
        UniqueConstraint("execution_id", "step_key", name="uq_job_step_key"),
    )

    execution_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("job_executions.id", ondelete="CASCADE"), nullable=False,
    )
    step_key: Mapped[str] = mapped_column(String(64), nullable=False)
    step_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False, default="ansible")
    # v37 删除 attempt_type（do|rollback）：无回滚则恒为 do，无信息量
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending",
    )  # pending|running|success|failed|skipped
    # job_steps.serial 保留为历史记录列（v30 起新行恒为 NULL，并发度归 runner）
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
