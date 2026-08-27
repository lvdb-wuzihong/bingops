"""变更封禁窗口业务服务（P3 风控栅栏，工单侧与任务侧共用）。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from bingops.core.exceptions import NotFoundError, ValidationError
from bingops.models.ticket import ChangeFreeze
from bingops.models.user import User
from bingops.repositories.ticket_repo import ChangeFreezeRepo
from bingops.schemas.ticket import FreezeCreate

logger = logging.getLogger(f"bingops.{__name__}")


async def list_freezes(session: AsyncSession, *, active_only: bool = False) -> list[ChangeFreeze]:
    """封禁窗口列表（可只看当前生效）。"""
    return await ChangeFreezeRepo(session).list_freezes(active_only=active_only)


async def create_freeze(
    session: AsyncSession, payload: FreezeCreate, operator: User,
) -> ChangeFreeze:
    """创建封禁窗口（校验时间区间合法性）。"""
    if payload.ends_at <= payload.starts_at:
        raise ValidationError("ends_at must be after starts_at")

    freeze = ChangeFreeze(
        name=payload.name,
        reason=payload.reason,
        scope=payload.scope,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        created_by=operator.id,
    )
    freeze = await ChangeFreezeRepo(session).create(freeze)
    await session.commit()

    logger.info(
        "Change freeze created",
        extra={"freeze_id": freeze.id, "scope": payload.scope, "user_id": operator.id},
    )
    return freeze


async def delete_freeze(session: AsyncSession, freeze_id: int, operator: User) -> None:
    """删除封禁窗口。"""
    repo = ChangeFreezeRepo(session)
    freeze = await repo.get_by_id(freeze_id)
    if freeze is None:
        raise NotFoundError("ChangeFreeze", str(freeze_id))

    await repo.delete(freeze)
    await session.commit()

    logger.info("Change freeze deleted", extra={"freeze_id": freeze_id, "user_id": operator.id})


def _freeze_hits_models(freeze: ChangeFreeze, model_codes: set[str]) -> bool:
    """封禁窗口是否命中给定模型集合（scope 为空 = 全局命中）。"""
    if not freeze.scope:
        return True
    return bool(model_codes & set(freeze.scope))


async def find_active_freezes_for_models(
    session: AsyncSession, model_codes: set[str], at: datetime | None = None,
) -> list[ChangeFreeze]:
    """返回当前时刻命中目标模型范围的生效封禁窗口（执行前门控用）。"""
    moment = at or datetime.now(timezone.utc)
    active = await ChangeFreezeRepo(session).list_freezes(active_only=True, at=moment)
    return [f for f in active if _freeze_hits_models(f, model_codes)]
