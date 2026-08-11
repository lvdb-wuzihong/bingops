"""工单系统业务服务。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from bingops.core.exceptions import NotFoundError, PermissionDeniedError, ValidationError
from bingops.models.cmdb.resource import CmdbResource
from bingops.models.ticket import Ticket, TicketComment
from bingops.models.user import User
from bingops.repositories.ticket_repo import TicketCommentRepo, TicketRepo
from bingops.repositories.user_repo import UserRepo
from bingops.schemas.ticket import TicketCreate, TicketUpdate

logger = logging.getLogger(f"bingops.{__name__}")

VALID_TICKET_TYPES = ("general", "request", "change", "incident")
VALID_PRIORITIES = ("low", "medium", "high", "urgent")

# 状态流转矩阵：当前状态 → 允许的目标状态
STATUS_TRANSITIONS: dict[str, set[str]] = {
    "open": {"in_progress", "cancelled"},
    "in_progress": {"resolved", "cancelled"},
    "resolved": {"closed", "in_progress"},
    "closed": set(),
    "cancelled": set(),
}

# 终态：不允许评论与任何变更
TERMINAL_STATUSES = ("closed", "cancelled")


async def _get_ticket_or_fail(session: AsyncSession, ticket_id: int) -> Ticket:
    """获取工单，不存在则抛出 NotFoundError。"""
    repo = TicketRepo(session)
    ticket = await repo.get_by_id(ticket_id)
    if ticket is None:
        raise NotFoundError("Ticket", str(ticket_id))
    return ticket


async def _get_user_or_fail(session: AsyncSession, user_id: int) -> User:
    """获取用户，不存在则抛出 NotFoundError。"""
    user = await UserRepo(session).get_by_id(user_id)
    if user is None:
        raise NotFoundError("User", str(user_id))
    return user


def _can_manage(ticket: Ticket, operator: User) -> bool:
    """判断操作人是否为创建人或超级管理员。"""
    return operator.is_superuser or ticket.creator_id == operator.id


async def list_tickets(
    session: AsyncSession,
    *,
    status: str | None = None,
    ticket_type: str | None = None,
    priority: str | None = None,
    creator_id: int | None = None,
    assignee_id: int | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Ticket], int]:
    """分页查询工单列表。"""
    repo = TicketRepo(session)
    return await repo.list_tickets(
        status=status,
        ticket_type=ticket_type,
        priority=priority,
        creator_id=creator_id,
        assignee_id=assignee_id,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )


async def get_ticket(session: AsyncSession, ticket_id: int) -> Ticket:
    """获取工单详情。"""
    return await _get_ticket_or_fail(session, ticket_id)


async def list_comments(session: AsyncSession, ticket_id: int) -> list[TicketComment]:
    """获取工单流转记录。"""
    await _get_ticket_or_fail(session, ticket_id)
    return await TicketCommentRepo(session).list_by_ticket(ticket_id)


async def create_ticket(session: AsyncSession, payload: TicketCreate, operator: User) -> Ticket:
    """创建工单并生成工单号。"""
    if payload.ticket_type not in VALID_TICKET_TYPES:
        raise ValidationError(f"ticket_type must be one of: {VALID_TICKET_TYPES}")
    if payload.priority not in VALID_PRIORITIES:
        raise ValidationError(f"priority must be one of: {VALID_PRIORITIES}")

    if payload.assignee_id is not None:
        await _get_user_or_fail(session, payload.assignee_id)
    if payload.related_resource_id is not None:
        resource = await session.get(CmdbResource, payload.related_resource_id)
        if resource is None:
            raise NotFoundError("CmdbResource", str(payload.related_resource_id))

    repo = TicketRepo(session)
    ticket = Ticket(
        ticket_no="",  # flush 后基于主键回填
        title=payload.title,
        description=payload.description,
        ticket_type=payload.ticket_type,
        status="open",
        priority=payload.priority,
        creator_id=operator.id,
        assignee_id=payload.assignee_id,
        related_resource_id=payload.related_resource_id,
    )
    ticket = await repo.create(ticket)
    ticket.ticket_no = f"TK-{ticket.id:08d}"
    await repo.update(ticket)

    await TicketCommentRepo(session).create(
        TicketComment(
            ticket_id=ticket.id,
            user_id=operator.id,
            action="create",
            content=payload.description,
        )
    )
    await session.commit()

    logger.info(
        "Ticket created",
        extra={"ticket_id": ticket.id, "ticket_no": ticket.ticket_no, "user_id": operator.id},
    )
    return ticket


async def update_ticket(
    session: AsyncSession, ticket_id: int, payload: TicketUpdate, operator: User,
) -> Ticket:
    """更新工单（仅 open 状态、创建人或管理员可操作）。"""
    ticket = await _get_ticket_or_fail(session, ticket_id)

    if ticket.status != "open":
        raise ValidationError("Ticket can only be updated in open status")
    if not _can_manage(ticket, operator):
        raise PermissionDeniedError("Only the creator or admin can update the ticket")
    if payload.priority is not None and payload.priority not in VALID_PRIORITIES:
        raise ValidationError(f"priority must be one of: {VALID_PRIORITIES}")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(ticket, field, value)

    ticket = await TicketRepo(session).update(ticket)
    await session.commit()

    logger.info("Ticket updated", extra={"ticket_id": ticket_id, "user_id": operator.id})
    return ticket


async def assign_ticket(
    session: AsyncSession, ticket_id: int, assignee_id: int, operator: User,
) -> Ticket:
    """指派/转派工单处理人。"""
    ticket = await _get_ticket_or_fail(session, ticket_id)

    if ticket.status not in ("open", "in_progress"):
        raise ValidationError("Ticket can only be assigned in open or in_progress status")

    assignee = await _get_user_or_fail(session, assignee_id)
    if not assignee.is_active:
        raise ValidationError("Assignee account is disabled")

    previous = ticket.assignee_id
    ticket.assignee_id = assignee_id
    ticket = await TicketRepo(session).update(ticket)

    await TicketCommentRepo(session).create(
        TicketComment(
            ticket_id=ticket.id,
            user_id=operator.id,
            action="assign",
            from_value=str(previous) if previous is not None else None,
            to_value=str(assignee_id),
        )
    )
    await session.commit()

    logger.info(
        "Ticket assigned",
        extra={"ticket_id": ticket_id, "assignee_id": assignee_id, "user_id": operator.id},
    )
    return ticket


async def change_ticket_status(
    session: AsyncSession, ticket_id: int, target_status: str, operator: User,
    comment: str | None = None,
) -> Ticket:
    """按状态流转矩阵推进工单状态。"""
    ticket = await _get_ticket_or_fail(session, ticket_id)

    allowed = STATUS_TRANSITIONS.get(ticket.status, set())
    if target_status not in allowed:
        raise ValidationError(
            f"Invalid status transition: {ticket.status} -> {target_status}"
        )

    now = datetime.now(timezone.utc)
    previous = ticket.status
    ticket.status = target_status
    if target_status == "resolved":
        ticket.resolved_at = now
    elif target_status == "closed":
        ticket.closed_at = now
    elif target_status == "in_progress" and previous == "resolved":
        # 重开时清空解决时间
        ticket.resolved_at = None

    ticket = await TicketRepo(session).update(ticket)

    await TicketCommentRepo(session).create(
        TicketComment(
            ticket_id=ticket.id,
            user_id=operator.id,
            action="status_change",
            content=comment,
            from_value=previous,
            to_value=target_status,
        )
    )
    await session.commit()

    logger.info(
        "Ticket status changed",
        extra={
            "ticket_id": ticket_id,
            "from_status": previous,
            "to_status": target_status,
            "user_id": operator.id,
        },
    )
    return ticket


async def add_comment(
    session: AsyncSession, ticket_id: int, content: str, operator: User,
) -> TicketComment:
    """添加工单评论（终态工单禁止评论）。"""
    ticket = await _get_ticket_or_fail(session, ticket_id)

    if ticket.status in TERMINAL_STATUSES:
        raise ValidationError("Cannot comment on a closed or cancelled ticket")

    comment = await TicketCommentRepo(session).create(
        TicketComment(
            ticket_id=ticket.id,
            user_id=operator.id,
            action="comment",
            content=content,
        )
    )
    await session.commit()

    logger.info("Ticket comment added", extra={"ticket_id": ticket_id, "user_id": operator.id})
    return comment


async def delete_ticket(session: AsyncSession, ticket_id: int, operator: User) -> None:
    """删除工单（仅 open 状态、创建人或管理员可操作）。"""
    ticket = await _get_ticket_or_fail(session, ticket_id)

    if ticket.status != "open":
        raise ValidationError("Only open tickets can be deleted")
    if not _can_manage(ticket, operator):
        raise PermissionDeniedError("Only the creator or admin can delete the ticket")

    await TicketRepo(session).delete(ticket)
    await session.commit()

    logger.info("Ticket deleted", extra={"ticket_id": ticket_id, "user_id": operator.id})
