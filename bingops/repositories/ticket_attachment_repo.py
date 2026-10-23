"""工单附件数据访问层。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.models.ticket import TicketAttachment
from bingops.models.user import User


class TicketAttachmentRepo:
    """工单附件 Repository（只追加 + 删除，无更新语义）。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, attachment: TicketAttachment) -> TicketAttachment:
        self._session.add(attachment)
        await self._session.flush()
        return attachment

    async def get_by_id(self, attachment_id: int) -> TicketAttachment | None:
        result = await self._session.execute(
            select(TicketAttachment).where(TicketAttachment.id == attachment_id)
        )
        return result.scalar_one_or_none()

    async def list_by_ticket(
        self, ticket_id: int,
    ) -> list[tuple[TicketAttachment, str | None]]:
        """按工单列出附件（含上传者显示名，前端列表可直接渲染）。"""
        result = await self._session.execute(
            select(TicketAttachment, User.display_name)
            .join(User, User.id == TicketAttachment.uploaded_by)
            .where(TicketAttachment.ticket_id == ticket_id)
            .order_by(TicketAttachment.created_at.asc(), TicketAttachment.id.asc())
        )
        return [(row[0], row[1]) for row in result.all()]

    async def count_by_ticket(self, ticket_id: int) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(TicketAttachment)
            .where(TicketAttachment.ticket_id == ticket_id)
        )
        return result.scalar() or 0

    async def delete(self, attachment: TicketAttachment) -> None:
        await self._session.delete(attachment)
