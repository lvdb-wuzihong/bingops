"""工单系统数据访问层。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bingops.models.ticket import ChangeFreeze, Ticket, TicketApproval, TicketCatalog, TicketComment

# 工单列表/详情的关系预加载（创建人/处理人/目录项含父分类/处理组）
_TICKET_LOAD_OPTIONS = (
    selectinload(Ticket.creator),
    selectinload(Ticket.assignee),
    selectinload(Ticket.catalog_item).selectinload(TicketCatalog.parent),
    selectinload(Ticket.group),
)


class TicketRepo:
    """工单 Repository。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, ticket_id: int) -> Ticket | None:
        result = await self._session.execute(
            select(Ticket)
            .options(*_TICKET_LOAD_OPTIONS)
            .where(Ticket.id == ticket_id)
        )
        return result.scalar_one_or_none()

    async def list_tickets(
        self,
        *,
        status: str | None = None,
        ticket_type: str | None = None,
        priority: str | None = None,
        creator_id: int | None = None,
        assignee_id: int | None = None,
        group_id: int | None = None,
        catalog_item_id: int | None = None,
        target_resource_id: int | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Ticket], int]:
        """分页查询工单列表。"""
        query = select(Ticket).options(*_TICKET_LOAD_OPTIONS)
        count_query = select(Ticket.id)

        if status:
            query = query.where(Ticket.status == status)
            count_query = count_query.where(Ticket.status == status)
        if ticket_type:
            query = query.where(Ticket.ticket_type == ticket_type)
            count_query = count_query.where(Ticket.ticket_type == ticket_type)
        if priority:
            query = query.where(Ticket.priority == priority)
            count_query = count_query.where(Ticket.priority == priority)
        if creator_id is not None:
            query = query.where(Ticket.creator_id == creator_id)
            count_query = count_query.where(Ticket.creator_id == creator_id)
        if assignee_id is not None:
            query = query.where(Ticket.assignee_id == assignee_id)
            count_query = count_query.where(Ticket.assignee_id == assignee_id)
        if group_id is not None:
            query = query.where(Ticket.group_id == group_id)
            count_query = count_query.where(Ticket.group_id == group_id)
        if catalog_item_id is not None:
            query = query.where(Ticket.catalog_item_id == catalog_item_id)
            count_query = count_query.where(Ticket.catalog_item_id == catalog_item_id)
        if target_resource_id is not None:
            # JSONB 包含语义：target_resource_ids @> [rid]
            contains_filter = Ticket.target_resource_ids.contains([target_resource_id])
            query = query.where(contains_filter)
            count_query = count_query.where(contains_filter)
        if keyword:
            like_pattern = f"%{keyword}%"
            keyword_filter = or_(
                Ticket.title.ilike(like_pattern),
                Ticket.ticket_no.ilike(like_pattern),
            )
            query = query.where(keyword_filter)
            count_query = count_query.where(keyword_filter)

        total_result = await self._session.execute(
            select(func.count()).select_from(count_query.subquery())
        )
        total = total_result.scalar() or 0

        query = query.order_by(Ticket.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)
        result = await self._session.execute(query)
        tickets = list(result.scalars().all())

        return tickets, total

    async def list_by_statuses(self, statuses: tuple[str, ...]) -> list[Ticket]:
        """按状态集合查询（变更上下文聚合用，不分页）。"""
        result = await self._session.execute(
            select(Ticket).where(Ticket.status.in_(statuses))
        )
        return list(result.scalars().all())

    async def create(self, ticket: Ticket) -> Ticket:
        self._session.add(ticket)
        await self._session.flush()
        return ticket

    async def update(self, ticket: Ticket) -> Ticket:
        await self._session.flush()
        return ticket

    async def delete(self, ticket: Ticket) -> None:
        await self._session.delete(ticket)
        await self._session.flush()


class TicketCommentRepo:
    """工单流转/评论记录 Repository。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_ticket(self, ticket_id: int) -> list[TicketComment]:
        """按工单查询流转记录（时间正序）。"""
        result = await self._session.execute(
            select(TicketComment)
            .options(selectinload(TicketComment.user))
            .where(TicketComment.ticket_id == ticket_id)
            .order_by(TicketComment.created_at.asc(), TicketComment.id.asc())
        )
        return list(result.scalars().all())

    async def create(self, comment: TicketComment) -> TicketComment:
        self._session.add(comment)
        await self._session.flush()
        return comment


class TicketApprovalRepo:
    """工单审批记录 Repository（只追加，不可修改/删除）。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_ticket(self, ticket_id: int) -> list[TicketApproval]:
        """按工单查询审批记录（时间正序）。"""
        result = await self._session.execute(
            select(TicketApproval)
            .options(selectinload(TicketApproval.approver))
            .where(TicketApproval.ticket_id == ticket_id)
            .order_by(TicketApproval.created_at.asc(), TicketApproval.id.asc())
        )
        return list(result.scalars().all())

    async def create(self, approval: TicketApproval) -> TicketApproval:
        self._session.add(approval)
        await self._session.flush()
        return approval


class ChangeFreezeRepo:
    """变更封禁窗口 Repository。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, freeze_id: int) -> ChangeFreeze | None:
        result = await self._session.execute(
            select(ChangeFreeze).where(ChangeFreeze.id == freeze_id)
        )
        return result.scalar_one_or_none()

    async def list_freezes(
        self, *, active_only: bool = False, at: datetime | None = None,
    ) -> list[ChangeFreeze]:
        """封禁窗口列表（默认按生效时间倒序；active_only 只返当前生效的）。"""
        query = select(ChangeFreeze)
        if active_only:
            moment = at or datetime.now(timezone.utc)
            query = query.where(
                ChangeFreeze.starts_at <= moment, ChangeFreeze.ends_at >= moment,
            )
        query = query.order_by(ChangeFreeze.starts_at.desc())
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def create(self, freeze: ChangeFreeze) -> ChangeFreeze:
        self._session.add(freeze)
        await self._session.flush()
        return freeze

    async def delete(self, freeze: ChangeFreeze) -> None:
        await self._session.delete(freeze)
        await self._session.flush()
