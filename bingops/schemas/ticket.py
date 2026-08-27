"""工单系统 Pydantic Schemas。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TicketCreate(BaseModel):
    """创建工单请求。"""

    title: str = Field(min_length=1, max_length=256, description="工单标题")
    description: str | None = Field(default=None, description="工单描述")
    ticket_type: str = Field(
        default="general", description="工单类型: general|request|change|incident",
    )
    priority: str = Field(default="medium", description="优先级: low|medium|high|urgent")
    assignee_id: int | None = Field(default=None, description="指派的处理人用户 ID")
    related_resource_id: int | None = Field(default=None, description="关联的 CMDB 资源 ID")
    runbook_id: int | None = Field(default=None, description="变更工单携带的 Runbook ID")
    job_params: dict = Field(default_factory=dict, description="执行参数（随审批通过后下发）")
    code_ref: str | None = Field(default=None, max_length=128, description="git tag 快照")


class TicketUpdate(BaseModel):
    """更新工单请求（仅 open 状态、创建人可操作）。"""

    title: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = None
    priority: str | None = None


class TicketAssignRequest(BaseModel):
    """指派/转派请求。"""

    assignee_id: int = Field(description="处理人用户 ID")


class TicketStatusRequest(BaseModel):
    """状态流转请求。"""

    status: str = Field(description="目标状态: in_progress|resolved|closed|cancelled")
    comment: str | None = Field(default=None, description="流转备注")


class TicketCommentCreate(BaseModel):
    """添加工单评论请求。"""

    content: str = Field(min_length=1, description="评论内容")


class ApprovalSubmit(BaseModel):
    """审批提交请求。"""

    action: str = Field(description="审批动作: approve|reject")
    comment: str | None = Field(default=None, description="审批意见")


class ApprovalResponse(BaseModel):
    """审批记录响应。"""

    id: int
    ticket_id: int
    approver_id: int
    approver_name: str | None = None
    action: str
    comment: str | None = None
    created_at: datetime


class FreezeCreate(BaseModel):
    """创建变更封禁窗口请求。"""

    name: str = Field(min_length=1, max_length=128, description="封禁窗口名称")
    reason: str | None = Field(default=None, description="封禁原因")
    scope: list[str] | None = Field(
        default=None, description="限定模型范围（如 [aliyun_ecs]），空为全局封禁",
    )
    starts_at: datetime = Field(description="生效开始时间")
    ends_at: datetime = Field(description="生效结束时间")


class FreezeResponse(BaseModel):
    """变更封禁窗口响应。"""

    id: int
    name: str
    reason: str | None = None
    scope: list[str] | None = None
    starts_at: datetime
    ends_at: datetime
    created_by: int | None = None
    created_at: datetime
    updated_at: datetime


class ChangeContextResource(BaseModel):
    """单个资源的变更上下文（判断变更时点用）。"""

    resource_id: int
    name: str | None = None
    model_code: str | None = None
    status: str | None = None
    env: str | None = Field(default=None, description="环境（标签解析，无则为空）")
    recent_changes: list[dict] = Field(
        default_factory=list, description="最近变更记录（change_log）",
    )
    busy_execution_id: int | None = Field(
        default=None, description="占用中的任务执行 ID（无则空）",
    )
    active_freezes: list[dict] = Field(
        default_factory=list, description="当前命中的封禁窗口",
    )


class TicketCommentResponse(BaseModel):
    """工单流转/评论记录响应。"""

    id: int
    ticket_id: int
    user_id: int
    user_name: str | None = None
    action: str
    content: str | None = None
    from_value: str | None = None
    to_value: str | None = None
    created_at: datetime


class TicketResponse(BaseModel):
    """工单响应。"""

    id: int
    ticket_no: str
    title: str
    description: str | None = None
    ticket_type: str
    status: str
    priority: str
    creator_id: int
    creator_name: str | None = None
    assignee_id: int | None = None
    assignee_name: str | None = None
    related_resource_id: int | None = None
    runbook_id: int | None = None
    job_params: dict = Field(default_factory=dict)
    code_ref: str | None = None
    approval_status: str | None = None
    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TicketDetailResponse(TicketResponse):
    """工单详情响应（含流转记录、审批记录、关联任务）。"""

    comments: list[TicketCommentResponse] = Field(default_factory=list)
    approvals: list[ApprovalResponse] = Field(default_factory=list)
    job_execution: dict | None = Field(
        default=None, description="审批通过后自动创建的任务执行摘要",
    )
