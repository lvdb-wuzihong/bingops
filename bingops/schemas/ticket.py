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
    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TicketDetailResponse(TicketResponse):
    """工单详情响应（含流转记录）。"""

    comments: list[TicketCommentResponse] = Field(default_factory=list)
