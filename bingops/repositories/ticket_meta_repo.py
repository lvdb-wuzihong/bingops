"""服务目录/处理组/值班表数据访问层。"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bingops.models.ticket import OncallSchedule, TicketCatalog, TicketGroup


class TicketCatalogRepo:
    """服务目录 Repository。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, item_id: int) -> TicketCatalog | None:
        result = await self._session.execute(
            select(TicketCatalog)
            .options(selectinload(TicketCatalog.parent))
            .where(TicketCatalog.id == item_id)
        )
        return result.scalar_one_or_none()

    async def list_items(
        self, *, parent_id: int | None = None, include_inactive: bool = False,
    ) -> list[TicketCatalog]:
        """目录列表（parent_id=None 返回全部；指定则过滤层级）。"""
        query = select(TicketCatalog).options(selectinload(TicketCatalog.parent))
        if not include_inactive:
            query = query.where(TicketCatalog.is_active.is_(True))
        if parent_id is not None:
            query = query.where(TicketCatalog.parent_id == parent_id)
        query = query.order_by(TicketCatalog.sort_order.asc(), TicketCatalog.id.asc())
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def create(self, item: TicketCatalog) -> TicketCatalog:
        self._session.add(item)
        await self._session.flush()
        return item

    async def update(self, item: TicketCatalog) -> TicketCatalog:
        await self._session.flush()
        return item

    async def delete(self, item: TicketCatalog) -> None:
        await self._session.delete(item)
        await self._session.flush()


class TicketGroupRepo:
    """处理组 Repository。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, group_id: int) -> TicketGroup | None:
        result = await self._session.execute(
            select(TicketGroup).where(TicketGroup.id == group_id)
        )
        return result.scalar_one_or_none()

    async def list_groups(self, *, include_inactive: bool = False) -> list[TicketGroup]:
        query = select(TicketGroup)
        if not include_inactive:
            query = query.where(TicketGroup.is_active.is_(True))
        query = query.order_by(TicketGroup.id.asc())
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def create(self, group: TicketGroup) -> TicketGroup:
        self._session.add(group)
        await self._session.flush()
        return group

    async def update(self, group: TicketGroup) -> TicketGroup:
        await self._session.flush()
        return group

    async def delete(self, group: TicketGroup) -> None:
        await self._session.delete(group)
        await self._session.flush()


class OncallScheduleRepo:
    """值班表 Repository。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, schedule_id: int) -> OncallSchedule | None:
        result = await self._session.execute(
            select(OncallSchedule)
            .options(selectinload(OncallSchedule.group))
            .where(OncallSchedule.id == schedule_id)
        )
        return result.scalar_one_or_none()

    async def get_by_group_and_date(
        self, group_id: int, oncall_date: date,
    ) -> OncallSchedule | None:
        """按组+日期查询（自动派单用）。"""
        result = await self._session.execute(
            select(OncallSchedule).where(
                OncallSchedule.group_id == group_id,
                OncallSchedule.oncall_date == oncall_date,
            )
        )
        return result.scalar_one_or_none()

    async def list_schedules(
        self,
        *,
        group_id: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[OncallSchedule]:
        query = select(OncallSchedule).options(selectinload(OncallSchedule.group))
        if group_id is not None:
            query = query.where(OncallSchedule.group_id == group_id)
        if date_from is not None:
            query = query.where(OncallSchedule.oncall_date >= date_from)
        if date_to is not None:
            query = query.where(OncallSchedule.oncall_date <= date_to)
        query = query.order_by(OncallSchedule.oncall_date.asc())
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def create(self, schedule: OncallSchedule) -> OncallSchedule:
        self._session.add(schedule)
        await self._session.flush()
        return schedule

    async def update(self, schedule: OncallSchedule) -> OncallSchedule:
        await self._session.flush()
        return schedule

    async def delete(self, schedule: OncallSchedule) -> None:
        await self._session.delete(schedule)
        await self._session.flush()
