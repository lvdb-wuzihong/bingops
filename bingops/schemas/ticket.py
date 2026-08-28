"""工单系统 Pydantic Schemas。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

VALID_DIFFICULTIES = ("simple", "medium", "hard")


class TicketCreate(BaseModel):
    """创建工单请求。"""

    title: str = Field(min_length=1, max_length=256, description="工单标题")
    description: str | None = Field(default=None, description="工单描述")
    ticket_type: str = Field(
        default="general", description="工单类型: general|request|change|incident",
    )
    priority: str = Field(default="medium", description="优先级: low|medium|high|urgent")
    assignee_id: int | None = Field(default=None, description="指派的处理人用户 ID")
    related_resource_id: int | None = Field(
        default=None, description="已废弃：关联资源 ID，改用 target_resource_ids（兼容保留）",
    )
    runbook_id: int | None = Field(default=None, description="变更工单携带的 Runbook ID")
    job_params: dict = Field(default_factory=dict, description="执行参数（随审批通过后下发）")
    code_ref: str | None = Field(default=None, max_length=128, description="git tag 快照")
    catalog_item_id: int | None = Field(default=None, description="服务目录事项 ID（二级）")
    group_id: int | None = Field(default=None, description="处理组 ID（驱动值班自动派单）")
    target_resource_ids: list[int] = Field(
        default_factory=list, description="执行目标资源 ID 列表（多选；绑 runbook 时必填）",
    )


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
    active_tickets: list[dict] = Field(
        default_factory=list,
        description="影响该资源的活跃工单（pending_approval/open/in_progress）",
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
    catalog_item_id: int | None = None
    catalog_item_name: str | None = None
    catalog_category_name: str | None = None
    group_id: int | None = None
    group_name: str | None = None
    difficulty: str | None = None
    started_at: datetime | None = None
    target_resource_ids: list[int] = Field(default_factory=list)
    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CatalogCreate(BaseModel):
    """创建服务目录项请求（通用兼容端点；推荐用 categories/items 语义端点）。"""

    name: str = Field(min_length=1, max_length=128)
    parent_id: int | None = Field(default=None, description="一级分类 ID，空=创建一级分类")
    description: str | None = None
    difficulty: str = Field(default="simple", description="simple|medium|hard")
    default_risk: str = Field(default="low", description="low|medium|high")
    default_type: str = Field(default="request", description="语义 ticket_type")
    default_runbook_id: int | None = None
    default_group_id: int | None = Field(default=None, description="默认处理组（路由配置化）")
    sort_order: int = 0


class CatalogUpdate(BaseModel):
    """更新服务目录项请求。"""

    description: str | None = None
    difficulty: str | None = None
    default_risk: str | None = None
    default_type: str | None = None
    default_runbook_id: int | None = None
    default_group_id: int | None = None
    is_active: bool | None = None
    sort_order: int | None = None


class CategoryCreate(BaseModel):
    """创建一级分类请求（不含事项级属性，含路由配置）。"""

    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    default_group_id: int | None = Field(default=None, description="默认处理组")
    sort_order: int = 0


class ItemCreate(BaseModel):
    """创建二级事项请求（parent_id 必填，携带事项级属性）。"""

    name: str = Field(min_length=1, max_length=128)
    parent_id: int = Field(description="所属一级分类 ID")
    description: str | None = None
    difficulty: str = Field(default="simple", description="simple|medium|hard")
    default_risk: str = Field(default="low", description="low|medium|high")
    default_type: str = Field(default="request", description="语义 ticket_type")
    default_runbook_id: int | None = None
    default_group_id: int | None = Field(default=None, description="覆盖分类的默认处理组")
    sort_order: int = 0


class CatalogResponse(BaseModel):
    """服务目录项响应。"""

    id: int
    name: str
    parent_id: int | None = None
    description: str | None = None
    difficulty: str
    default_risk: str
    default_type: str
    default_runbook_id: int | None = None
    default_group_id: int | None = None
    is_active: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime


class GroupCreate(BaseModel):
    """创建处理组请求。"""

    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    members: list[int] = Field(default_factory=list, description="用户 ID 数组")


class GroupUpdate(BaseModel):
    """更新处理组请求。"""

    description: str | None = None
    members: list[int] | None = None
    is_active: bool | None = None


class GroupResponse(BaseModel):
    """处理组响应。"""

    id: int
    name: str
    description: str | None = None
    members: list[int] = Field(default_factory=list)
    is_active: bool
    created_at: datetime
    updated_at: datetime


class OncallCreate(BaseModel):
    """创建值班排班请求。"""

    group_id: int
    oncall_date: datetime = Field(description="值班日期")
    tier1: list[int] = Field(default_factory=list, description="一线值班用户 ID")
    tier2: list[int] = Field(default_factory=list, description="二线支持用户 ID")
    tier3: list[int] = Field(default_factory=list, description="三线支持用户 ID")
    note: str | None = None


class OncallUpdate(BaseModel):
    """更新值班排班请求。"""

    tier1: list[int] | None = None
    tier2: list[int] | None = None
    tier3: list[int] | None = None
    note: str | None = None


class OncallResponse(BaseModel):
    """值班排班响应。"""

    id: int
    group_id: int
    group_name: str | None = None
    oncall_date: datetime
    tier1: list[int] = Field(default_factory=list)
    tier2: list[int] = Field(default_factory=list)
    tier3: list[int] = Field(default_factory=list)
    note: str | None = None
    created_at: datetime
    updated_at: datetime


class TicketDetailResponse(TicketResponse):
    """工单详情响应（含流转记录、审批记录、关联任务）。"""

    comments: list[TicketCommentResponse] = Field(default_factory=list)
    approvals: list[ApprovalResponse] = Field(default_factory=list)
    job_execution: dict | None = Field(
        default=None, description="审批通过后自动创建的任务执行摘要",
    )
