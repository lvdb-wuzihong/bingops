"""任务系统 Pydantic 模型（API DTO + Kafka 消息契约）。

Kafka 契约见 docs/task-system-design.md §9.2：
- job-dispatch：bingops → runner（v37 起只有“执行”一种语义，command 字段已删）
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
    """创建 Runbook（v29 扁平单步：一个 runbook = 一个步骤）。

    必填只两项：`exec_type`（执行方式，UI 下拉）+ `entry`（入口）。
    entry 语义随 exec_type 变：ansible=playbook 路径、shell=**内联命令**、
    script=**仓库内脚本文件路径**（runner 拉仓库后推送执行，目标机不需预置该文件）、
    python=脚本入口、terraform=工作目录。
    不再接收 `steps` 数组（多步编排不开放）。
    """

    name: str = Field(max_length=128)
    exec_type: str = Field(
        description="ansible | shell | script | python | terraform"
    )
    entry: str = Field(
        description="playbook 路径 / 内联命令 / 仓库脚本路径 / python 脚本 / tf 目录"
    )
    category: str | None = None
    description: str | None = None
    params_schema: dict = Field(default_factory=dict)
    # 需走 Vault 的入参声明 {变量名: {required, description, default_ref}}（v27 凭据三层分离）
    secrets_schema: dict = Field(default_factory=dict)
    # ── 步骤属性（均有安全缺省值，创建表单不必出现）──
    run_on: str | None = None      # None → 按 exec_type 推断；target=SSH 目标机，local=runner 本机
    timeout_sec: int | None = None  # None → 600
    # v30：undo_command / serial / batch_pause_sec 已删除；多目标并发度由执行机自身配置决定
    # v37：rollbackable 也已删除——平台不提供回滚，失败由人根据日志修复
    # v34：连接三件套（登录用户/密钥/提权）已全部撤到执行面——
    # 「以谁的身份连」在执行时才确定，不属于任务定义
    # ── 以下均有安全缺省值 ──
    target_models: list[str] | None = None  # None → 默认 [aliyun_ecs, gcp_compute]
    risk_level: str = "low"
    # v36：default_target_resource_ids / default_code_ref 已删除——目标机与版本
    # 属于每次执行，不得缓存在模板上（前端改用「复用上次的」读执行历史）


class RunbookUpdate(BaseModel):
    name: str | None = None
    exec_type: str | None = None
    entry: str | None = None
    category: str | None = None
    description: str | None = None
    params_schema: dict | None = None
    secrets_schema: dict | None = None
    run_on: str | None = None
    timeout_sec: int | None = None
    target_models: list[str] | None = None
    risk_level: str | None = None
    is_active: bool | None = None


class RunbookResponse(BaseModel):
    id: int
    name: str
    category: str | None
    description: str | None
    params_schema: dict
    secrets_schema: dict
    # 扁平单步（v29）：步骤列直接回显，不再有 steps 数组
    exec_type: str
    entry: str
    run_on: str
    timeout_sec: int
    connection: dict
    target_models: list
    version: int
    risk_level: str
    is_active: bool
    created_by: int | None
    created_at: datetime
    updated_at: datetime


# ── Execution DTO ─────────────────────────────────────────────────────────────


class ExecutionCreate(BaseModel):
    runbook_id: int
    params: dict = Field(default_factory=dict)
    # 需走 Vault 的入参：{变量名: Vault 钥匙名}（未传则用 secrets_schema.default_ref 回填）
    secrets: dict = Field(default_factory=dict)
    # 目标机：v36 起必须每次显式传（runbook 已无默认绑定），无 target 型步骤的任务可空
    target_resource_ids: list[int] | None = None
    # 仓库版本：ansible/script/python 型必填（显式传 > 平台配置 > 400）；
    # shell 的内联命令不依赖仓库代码，可留空（v38）
    code_ref: str | None = Field(default=None, max_length=128)
    ticket_id: int | None = None  # P3：高危 runbook 必须携带已审批通过的工单
    # ── 连接三件套（v34：执行时填写，不进 runbook 定义）──
    # 登录用户：未填 → runbook.connection.ssh_user 存量兜底 → 400
    ssh_user: str | None = Field(default=None, max_length=64)
    # 凭据目录条目名（kind=ssh_key），后端展开成 Vault 引用；
    # 未填 → runbook.connection.ssh_key_ref 存量兜底 → 400
    ssh_credential: str | None = Field(default=None, max_length=128)
    # 提权：未填 → false（runbook.connection.become 存量兜底）
    become: bool | None = None
    # 中转网关：未填 → 按机器归属自动选路；填 name 强制全员走该网关
    gateway_name: str | None = Field(default=None, max_length=128)


class ExecutionTarget(BaseModel):
    resource_id: int
    name: str
    ip: str | None = None
    region: str | None = None
    model_code: str | None = None
    cluster_id: str | None = None   # K8s 模式（P2）：目标所属集群
    namespace: str | None = None    # K8s 模式（P2）：命名空间
    # v31：凭据由执行面解析后逐台携带（同一执行内同值），
    # runner 优先用这里的值，connection 退化为汇总兜底
    ssh_user: str | None = None
    ssh_key_ref: str | None = None
    # 中转网关（v32 接入）：为空 = 直连
    gateway: dict | None = None


class ExecutionResponse(BaseModel):
    id: int
    runbook_id: int
    runbook_version: int
    code_ref: str
    params: dict
    secrets: dict
    target_resources: list
    connection: dict
    status: str
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
    """下发步骤（v29：单步对象，不再是数组）；字段与 runbooks 步骤列一一对应。"""

    key: str = "main"
    name: str | None = None
    type: str = "ansible"
    # 执行位置：target = SSH 到目标机；local = runner 本机
    run_on: str = "target"
    # 执行入口：路径类（playbook/仓库脚本/tf 目录）或 shell 的内联命令
    entry: str = ""
    timeout_sec: int | None = None
    # v37：rollbackable 已删除——平台不下发回滚


class JobDispatchMessage(BaseModel):
    message_id: str
    # v37：command 字段已删除——取消（cancel）不下发，消息只剩“执行”一种语义
    execution_id: int
    # git tag 快照；**空串 = 本执行不依赖仓库代码**（shell 内联命令），runner 据此跳过 clone
    code_ref: str
    params: dict = Field(default_factory=dict)
    # 只带钥匙名，真钥匙由 runner 现场去 Vault 取（v27：与 params 分开的显式密钥集）
    secrets: dict = Field(default_factory=dict)
    # 钥匙名进消息，真钥匙由 runner 现场去 Vault 取
    connection: dict = Field(default_factory=dict)
    targets: list[ExecutionTarget] = Field(default_factory=list)
    step: DispatchStep


# ── Kafka 消息：job-events（runner → bingops）─────────────────────────────────


class JobEventMessage(BaseModel):
    message_id: str
    execution_id: int
    step_key: str | None = None
    # v37：attempt_type（do|rollback）已删除——一步一行，不存在第二次尝试
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
