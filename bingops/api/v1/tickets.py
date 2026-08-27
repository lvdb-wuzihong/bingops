"""工单系统 API 路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.api.dependencies import get_db_session, require_permission
from bingops.core.exceptions import ValidationError
from bingops.core.response import paginated_response, success_response
from bingops.models.ticket import ChangeFreeze, Ticket, TicketApproval, TicketComment
from bingops.models.user import User
from bingops.repositories.jobs_repo import JobExecutionRepo
from bingops.schemas.ticket import (
    ApprovalResponse,
    ApprovalSubmit,
    FreezeCreate,
    FreezeResponse,
    TicketAssignRequest,
    TicketCommentCreate,
    TicketCommentResponse,
    TicketCreate,
    TicketDetailResponse,
    TicketResponse,
    TicketStatusRequest,
    TicketUpdate,
)
from bingops.services import change_freeze_service, ticket_service

router = APIRouter(prefix="/api/v1/tickets", tags=["tickets"])


def _user_display_name(user: User | None) -> str | None:
    """提取用户显示名（display_name 缺失时回退 username）。"""
    if user is None:
        return None
    return user.display_name or user.username


def _to_response(ticket: Ticket) -> dict:
    """ORM 工单转响应字典。"""
    return TicketResponse(
        id=ticket.id,
        ticket_no=ticket.ticket_no,
        title=ticket.title,
        description=ticket.description,
        ticket_type=ticket.ticket_type,
        status=ticket.status,
        priority=ticket.priority,
        creator_id=ticket.creator_id,
        creator_name=_user_display_name(ticket.creator),
        assignee_id=ticket.assignee_id,
        assignee_name=_user_display_name(ticket.assignee),
        related_resource_id=ticket.related_resource_id,
        runbook_id=ticket.runbook_id,
        job_params=ticket.job_params,
        code_ref=ticket.code_ref,
        approval_status=ticket.approval_status,
        resolved_at=ticket.resolved_at,
        closed_at=ticket.closed_at,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
    ).model_dump(mode="json")


def _comment_to_response(comment: TicketComment) -> dict:
    """ORM 流转记录转响应字典。"""
    return TicketCommentResponse(
        id=comment.id,
        ticket_id=comment.ticket_id,
        user_id=comment.user_id,
        user_name=_user_display_name(comment.user),
        action=comment.action,
        content=comment.content,
        from_value=comment.from_value,
        to_value=comment.to_value,
        created_at=comment.created_at,
    ).model_dump(mode="json")


def _approval_to_response(approval: TicketApproval) -> dict:
    """ORM 审批记录转响应字典。"""
    return ApprovalResponse(
        id=approval.id,
        ticket_id=approval.ticket_id,
        approver_id=approval.approver_id,
        approver_name=_user_display_name(approval.approver),
        action=approval.action,
        comment=approval.comment,
        created_at=approval.created_at,
    ).model_dump(mode="json")


def _freeze_to_response(freeze: ChangeFreeze) -> dict:
    """ORM 封禁窗口转响应字典。"""
    return FreezeResponse(
        id=freeze.id,
        name=freeze.name,
        reason=freeze.reason,
        scope=freeze.scope,
        starts_at=freeze.starts_at,
        ends_at=freeze.ends_at,
        created_by=freeze.created_by,
        created_at=freeze.created_at,
        updated_at=freeze.updated_at,
    ).model_dump(mode="json")


@router.get("")
async def list_tickets(
    status: str | None = None,
    ticket_type: str | None = None,
    priority: str | None = None,
    creator_id: int | None = None,
    assignee_id: int | None = None,
    keyword: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("ticket:list"),
):
    """查询工单列表（分页）。"""
    tickets, total = await ticket_service.list_tickets(
        session,
        status=status,
        ticket_type=ticket_type,
        priority=priority,
        creator_id=creator_id,
        assignee_id=assignee_id,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
    items = [_to_response(t) for t in tickets]
    return paginated_response(items, total, page, page_size)


@router.post("", status_code=201)
async def create_ticket(
    payload: TicketCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = require_permission("ticket:create"),
):
    """创建工单。"""
    ticket = await ticket_service.create_ticket(session, payload, current_user)
    return success_response(data=_to_response(ticket), message="Ticket created", http_status=201)


@router.get("/change-context")
async def get_change_context(
    resource_ids: str = Query(description="逗号分隔的资源 ID 列表"),
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("ticket:list"),
):
    """变更上下文聚合：近期变更/占用任务/封禁窗口/环境（判断变更时点用）。"""
    try:
        ids = [int(part) for part in resource_ids.split(",") if part.strip()]
    except ValueError:
        raise ValidationError("resource_ids must be comma-separated integers")
    items = await ticket_service.change_context(session, ids)
    return success_response(data=[item.model_dump(mode="json") for item in items])


@router.get("/freezes")
async def list_freezes(
    active_only: bool = False,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("change_freeze:list"),
):
    """变更封禁窗口列表。"""
    freezes = await change_freeze_service.list_freezes(session, active_only=active_only)
    return success_response(data=[_freeze_to_response(f) for f in freezes])


@router.post("/freezes", status_code=201)
async def create_freeze(
    payload: FreezeCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = require_permission("change_freeze:create"),
):
    """创建变更封禁窗口。"""
    freeze = await change_freeze_service.create_freeze(session, payload, current_user)
    return success_response(
        data=_freeze_to_response(freeze), message="Change freeze created", http_status=201,
    )


@router.delete("/freezes/{freeze_id}")
async def delete_freeze(
    freeze_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = require_permission("change_freeze:delete"),
):
    """删除变更封禁窗口。"""
    await change_freeze_service.delete_freeze(session, freeze_id, current_user)
    return success_response(message="Change freeze deleted")


@router.get("/{ticket_id}")
async def get_ticket(
    ticket_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("ticket:list"),
):
    """获取工单详情（含流转记录、审批记录、关联任务执行）。"""
    ticket = await ticket_service.get_ticket(session, ticket_id)
    comments = await ticket_service.list_comments(session, ticket_id)
    approvals = await ticket_service.list_approvals(session, ticket_id)
    execution = await JobExecutionRepo(session).get_latest_by_ticket(ticket_id)
    job_summary = None
    if execution is not None:
        job_summary = {
            "id": execution.id,
            "runbook_id": execution.runbook_id,
            "status": execution.status,
            "started_at": execution.started_at.isoformat() if execution.started_at else None,
            "finished_at": (
                execution.finished_at.isoformat() if execution.finished_at else None
            ),
        }
    detail = TicketDetailResponse(
        **_to_response(ticket),
        comments=[_comment_to_response(c) for c in comments],
        approvals=[_approval_to_response(a) for a in approvals],
        job_execution=job_summary,
    )
    return success_response(data=detail.model_dump(mode="json"))


@router.put("/{ticket_id}")
async def update_ticket(
    ticket_id: int,
    payload: TicketUpdate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = require_permission("ticket:update"),
):
    """更新工单（仅 open 状态、创建人或管理员）。"""
    ticket = await ticket_service.update_ticket(session, ticket_id, payload, current_user)
    return success_response(data=_to_response(ticket))


@router.delete("/{ticket_id}")
async def delete_ticket(
    ticket_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = require_permission("ticket:delete"),
):
    """删除工单（仅 open 状态、创建人或管理员）。"""
    await ticket_service.delete_ticket(session, ticket_id, current_user)
    return success_response(message="Ticket deleted")


@router.post("/{ticket_id}/assign")
async def assign_ticket(
    ticket_id: int,
    payload: TicketAssignRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = require_permission("ticket:assign"),
):
    """指派/转派工单处理人。"""
    ticket = await ticket_service.assign_ticket(
        session, ticket_id, payload.assignee_id, current_user,
    )
    return success_response(data=_to_response(ticket), message="Ticket assigned")


@router.post("/{ticket_id}/approve")
async def approve_ticket(
    ticket_id: int,
    payload: ApprovalSubmit,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = require_permission("ticket:approve"),
):
    """提交审批：通过→转 open（携带 runbook 时自动下发）；拒绝→cancelled。"""
    ticket = await ticket_service.submit_approval(
        session, ticket_id, payload.action, current_user, comment=payload.comment,
    )
    return success_response(data=_to_response(ticket), message=f"Ticket {payload.action}d")


@router.post("/{ticket_id}/status")
async def change_ticket_status(
    ticket_id: int,
    payload: TicketStatusRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = require_permission("ticket:update"),
):
    """推进工单状态（按流转矩阵校验）。"""
    ticket = await ticket_service.change_ticket_status(
        session, ticket_id, payload.status, current_user, comment=payload.comment,
    )
    return success_response(data=_to_response(ticket), message="Ticket status changed")


@router.get("/{ticket_id}/comments")
async def list_ticket_comments(
    ticket_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("ticket:list"),
):
    """获取工单流转/评论记录。"""
    comments = await ticket_service.list_comments(session, ticket_id)
    items = [_comment_to_response(c) for c in comments]
    return success_response(data=items)


@router.post("/{ticket_id}/comments", status_code=201)
async def add_ticket_comment(
    ticket_id: int,
    payload: TicketCommentCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = require_permission("ticket:create"),
):
    """添加工单评论。"""
    comment = await ticket_service.add_comment(session, ticket_id, payload.content, current_user)
    return success_response(
        data=_comment_to_response(comment), message="Ticket comment added", http_status=201,
    )
