"""服务目录/处理组/值班表业务服务。"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.core.exceptions import ConflictError, NotFoundError, ValidationError
from bingops.models.ticket import OncallSchedule, Ticket, TicketCatalog, TicketGroup
from bingops.repositories.ticket_meta_repo import (
    OncallScheduleRepo,
    TicketCatalogRepo,
    TicketGroupRepo,
)
from bingops.schemas.ticket import (
    VALID_DIFFICULTIES,
    CatalogCreate,
    CatalogUpdate,
    GroupCreate,
    GroupUpdate,
    OncallCreate,
    OncallUpdate,
)

logger = logging.getLogger(f"bingops.{__name__}")

VALID_RISKS = ("low", "medium", "high")
VALID_TYPES = ("general", "request", "change", "incident")


# ── 服务目录 ──────────────────────────────────────────────────────────────────


async def list_catalog(
    session: AsyncSession, *, parent_id: int | None = None, include_inactive: bool = False,
) -> list[TicketCatalog]:
    """服务目录列表。"""
    return await TicketCatalogRepo(session).list_items(
        parent_id=parent_id, include_inactive=include_inactive,
    )


async def create_catalog_item(session: AsyncSession, payload: CatalogCreate) -> TicketCatalog:
    """创建目录项（最多两级：parent 必须是一级分类）。"""
    if payload.difficulty not in VALID_DIFFICULTIES:
        raise ValidationError(f"difficulty must be one of: {VALID_DIFFICULTIES}")
    if payload.default_risk not in VALID_RISKS:
        raise ValidationError(f"default_risk must be one of: {VALID_RISKS}")
    if payload.default_type not in VALID_TYPES:
        raise ValidationError(f"default_type must be one of: {VALID_TYPES}")

    repo = TicketCatalogRepo(session)
    if payload.parent_id is not None:
        parent = await repo.get_by_id(payload.parent_id)
        if parent is None:
            raise NotFoundError("TicketCatalog", str(payload.parent_id))
        if parent.parent_id is not None:
            raise ValidationError("Catalog supports at most two levels")

    item = TicketCatalog(
        name=payload.name,
        parent_id=payload.parent_id,
        description=payload.description,
        difficulty=payload.difficulty,
        default_risk=payload.default_risk,
        default_type=payload.default_type,
        default_runbook_id=payload.default_runbook_id,
        sort_order=payload.sort_order,
    )
    item = await repo.create(item)
    await session.commit()

    logger.info(
        "Catalog item created",
        extra={"item_id": item.id, "name": payload.name, "parent_id": payload.parent_id},
    )
    return item


async def update_catalog_item(
    session: AsyncSession, item_id: int, payload: CatalogUpdate,
) -> TicketCatalog:
    """更新目录项。"""
    repo = TicketCatalogRepo(session)
    item = await repo.get_by_id(item_id)
    if item is None:
        raise NotFoundError("TicketCatalog", str(item_id))

    data = payload.model_dump(exclude_unset=True)
    if "difficulty" in data and data["difficulty"] not in VALID_DIFFICULTIES:
        raise ValidationError(f"difficulty must be one of: {VALID_DIFFICULTIES}")
    if "default_risk" in data and data["default_risk"] not in VALID_RISKS:
        raise ValidationError(f"default_risk must be one of: {VALID_RISKS}")
    for field, value in data.items():
        setattr(item, field, value)

    item = await repo.update(item)
    await session.commit()

    logger.info("Catalog item updated", extra={"item_id": item_id})
    return item


async def delete_catalog_item(session: AsyncSession, item_id: int) -> None:
    """删除目录项（有子项或被工单引用时禁止）。"""
    repo = TicketCatalogRepo(session)
    item = await repo.get_by_id(item_id)
    if item is None:
        raise NotFoundError("TicketCatalog", str(item_id))

    children = await repo.list_items(parent_id=item_id, include_inactive=True)
    if children:
        raise ConflictError("TicketCatalog", f"item {item_id} has children, delete them first")

    used = await session.execute(
        select(func.count()).select_from(Ticket).where(Ticket.catalog_item_id == item_id)
    )
    if (used.scalar() or 0) > 0:
        raise ConflictError("TicketCatalog", f"item {item_id} is referenced by tickets")

    await repo.delete(item)
    await session.commit()

    logger.info("Catalog item deleted", extra={"item_id": item_id})


# ── 处理组 ────────────────────────────────────────────────────────────────────


async def list_groups(
    session: AsyncSession, *, include_inactive: bool = False,
) -> list[TicketGroup]:
    """处理组列表。"""
    return await TicketGroupRepo(session).list_groups(include_inactive=include_inactive)


async def create_group(session: AsyncSession, payload: GroupCreate) -> TicketGroup:
    """创建处理组。"""
    repo = TicketGroupRepo(session)
    existing = await session.execute(
        select(TicketGroup).where(TicketGroup.name == payload.name)
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError("TicketGroup", f"group name already exists: {payload.name}")

    group = TicketGroup(
        name=payload.name, description=payload.description, members=payload.members,
    )
    group = await repo.create(group)
    await session.commit()

    logger.info("Ticket group created", extra={"group_id": group.id, "name": payload.name})
    return group


async def update_group(session: AsyncSession, group_id: int, payload: GroupUpdate) -> TicketGroup:
    """更新处理组。"""
    repo = TicketGroupRepo(session)
    group = await repo.get_by_id(group_id)
    if group is None:
        raise NotFoundError("TicketGroup", str(group_id))

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(group, field, value)

    group = await repo.update(group)
    await session.commit()

    logger.info("Ticket group updated", extra={"group_id": group_id})
    return group


async def delete_group(session: AsyncSession, group_id: int) -> None:
    """删除处理组（被工单引用时禁止；值班排班级联删除）。"""
    repo = TicketGroupRepo(session)
    group = await repo.get_by_id(group_id)
    if group is None:
        raise NotFoundError("TicketGroup", str(group_id))

    used = await session.execute(
        select(func.count()).select_from(Ticket).where(Ticket.group_id == group_id)
    )
    if (used.scalar() or 0) > 0:
        raise ConflictError("TicketGroup", f"group {group_id} is referenced by tickets")

    await repo.delete(group)
    await session.commit()

    logger.info("Ticket group deleted", extra={"group_id": group_id})


# ── 值班表 ────────────────────────────────────────────────────────────────────


async def list_oncall(
    session: AsyncSession,
    *,
    group_id: int | None = None,
    date_from=None,
    date_to=None,
) -> list[OncallSchedule]:
    """值班排班列表。"""
    return await OncallScheduleRepo(session).list_schedules(
        group_id=group_id, date_from=date_from, date_to=date_to,
    )


async def create_oncall(session: AsyncSession, payload: OncallCreate) -> OncallSchedule:
    """创建值班排班（同组同日期唯一）。"""
    group = await TicketGroupRepo(session).get_by_id(payload.group_id)
    if group is None:
        raise NotFoundError("TicketGroup", str(payload.group_id))

    repo = OncallScheduleRepo(session)
    oncall_date = payload.oncall_date.date()
    existing = await repo.get_by_group_and_date(payload.group_id, oncall_date)
    if existing is not None:
        raise ConflictError(
            "OncallSchedule",
            f"schedule already exists for group {payload.group_id} on {oncall_date}",
        )

    schedule = OncallSchedule(
        group_id=payload.group_id,
        oncall_date=oncall_date,
        tier1=payload.tier1,
        tier2=payload.tier2,
        tier3=payload.tier3,
        note=payload.note,
    )
    schedule = await repo.create(schedule)
    await session.commit()

    logger.info(
        "Oncall schedule created",
        extra={"schedule_id": schedule.id, "group_id": payload.group_id, "date": str(oncall_date)},
    )
    return schedule


async def update_oncall(
    session: AsyncSession, schedule_id: int, payload: OncallUpdate,
) -> OncallSchedule:
    """更新值班排班。"""
    repo = OncallScheduleRepo(session)
    schedule = await repo.get_by_id(schedule_id)
    if schedule is None:
        raise NotFoundError("OncallSchedule", str(schedule_id))

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(schedule, field, value)

    schedule = await repo.update(schedule)
    await session.commit()

    logger.info("Oncall schedule updated", extra={"schedule_id": schedule_id})
    return schedule


async def delete_oncall(session: AsyncSession, schedule_id: int) -> None:
    """删除值班排班。"""
    repo = OncallScheduleRepo(session)
    schedule = await repo.get_by_id(schedule_id)
    if schedule is None:
        raise NotFoundError("OncallSchedule", str(schedule_id))

    await repo.delete(schedule)
    await session.commit()

    logger.info("Oncall schedule deleted", extra={"schedule_id": schedule_id})
