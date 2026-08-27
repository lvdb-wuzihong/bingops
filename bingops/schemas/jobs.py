"""任务系统 Pydantic 模型（API DTO + Kafka 消息契约）。

Kafka 契约见 docs/task-system-design.md §9.2：
- job-dispatch：bingops → runner（command=execute|rollback）
- job-events：runner → bingops（step_started|log|step_finished|execution_finished）
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# ── Kafka Topic 常量 ──────────────────────────────────────────────────────────

JOB_DISPATCH_TOPIC = "job-dispatch"
JOB_EVENTS_TOPIC = "job-events"


# ── Runbook DTO ───────────────────────────────────────────────────────────────


class RunbookCreate(BaseModel):
    name: str = Field(max_length=128)
    category: str | None = None
    description: str | None = None
    params_schema: dict = Field(default_factory=dict)
    steps: list[dict] = Field(min_length=1)
    connection: dict = Field(default_factory=dict)
    target_models: list[str] | None = None  # None → 默认 [aliyun_ecs, gcp_compute]
    risk_level: str = "low"
    auto_rollback: bool = False


class RunbookUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    description: str | None = None
    params_schema: dict | None = None
    steps: list[dict] | None = None
    connection: dict | None = None
    target_models: list[str] | None = None
    risk_level: str | None = None
    auto_rollback: bool | None = None
    is_active: bool | None = None


class RunbookResponse(BaseModel):
    id: int
    name: str
    category: str | None
    description: str | None
    params_schema: dict
    steps: list
    connection: dict
    target_models: list
    version: int
    risk_level: str
    auto_rollback: bool
    is_active: bool
    created_by: int | None
    created_at: datetime
    updated_at: datetime


# ── Execution DTO ─────────────────────────────────────────────────────────────


class ExecutionCreate(BaseModel):
    runbook_id: int
    params: dict = Field(default_factory=dict)
    target_resource_ids: list[int] = Field(min_length=1)
    code_ref: str = Field(max_length=128)  # git tag
    ticket_id: int | None = None  # P3：高危 runbook 必须携带已审批通过的工单


class ExecutionTarget(BaseModel):
    resource_id: int
    name: str
    ip: str | None = None
    region: str | None = None
    model_code: str | None = None
    cluster_id: str | None = None   # K8s 模式（P2）：目标所属集群
    namespace: str | None = None    # K8s 模式（P2）：命名空间


class ExecutionResponse(BaseModel):
    id: int
    runbook_id: int
    runbook_version: int
    code_ref: str
    params: dict
    target_resources: list
    connection: dict
    status: str
    rollback_policy: str
    ticket_id: int | None
    triggered_by: int
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class StepResponse(BaseModel):
    id: int
    execution_id: int
    step_key: str
    step_name: str | None
    type: str
    attempt_type: str
    status: str
    serial: str | None
    exit_code: int | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None


class ExecutionDetailResponse(ExecutionResponse):
    steps: list[StepResponse] = Field(default_factory=list)


class StepLogResponse(BaseModel):
    id: int
    step_id: int
    seq: int
    level: str
    host: str | None
    line: str
    logged_at: datetime


# ── Kafka 消息：job-dispatch（bingops → runner）──────────────────────────────


class DispatchStep(BaseModel):
    key: str
    name: str | None = None
    type: str = "ansible"
    playbook: str
    timeout_sec: int | None = None
    serial: str | None = None
    batch_pause_sec: int | None = None
    rollbackable: bool = False


class JobDispatchMessage(BaseModel):
    message_id: str
    command: str  # execute | rollback
    execution_id: int
    code_ref: str
    params: dict = Field(default_factory=dict)
    # 只带钥匙名，真钥匙由 runner 现场去 Vault 取
    connection: dict = Field(default_factory=dict)
    targets: list[ExecutionTarget] = Field(default_factory=list)
    steps: list[DispatchStep] = Field(default_factory=list)


# ── Kafka 消息：job-events（runner → bingops）─────────────────────────────────


class JobEventMessage(BaseModel):
    message_id: str
    execution_id: int
    step_key: str | None = None
    attempt_type: str = "do"
    # step_started | log | step_finished | execution_finished
    event_type: str
    seq: int | None = None
    level: str = "info"
    host: str | None = None
    line: str | None = None
    status: str | None = None
    exit_code: int | None = None
    error: str | None = None
    timestamp: datetime | None = None
