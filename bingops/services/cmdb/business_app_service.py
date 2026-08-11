"""CMDB 业务应用管理服务。"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from bingops.core.exceptions import ConflictError, NotFoundError
from bingops.models.cmdb.business_app import CmdbBusinessApp
from bingops.repositories.cmdb.business_app_repo import CmdbBusinessAppRepo
from bingops.schemas.cmdb.business_app import BusinessAppCreate, BusinessAppUpdate

logger = logging.getLogger(f"bingops.{__name__}")


async def list_apps(
    session: AsyncSession,
    *,
    team: str | None = None,
    owner: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[CmdbBusinessApp], int]:
    """分页查询业务应用列表。"""
    repo = CmdbBusinessAppRepo(session)
    return await repo.list_apps(team=team, owner=owner, keyword=keyword, page=page, page_size=page_size)


async def get_app(session: AsyncSession, app_id: int) -> CmdbBusinessApp:
    """获取业务应用详情。"""
    repo = CmdbBusinessAppRepo(session)
    app = await repo.get_by_id(app_id)
    if app is None:
        raise NotFoundError("CmdbBusinessApp", str(app_id))
    return app


async def create_app(session: AsyncSession, payload: BusinessAppCreate) -> CmdbBusinessApp:
    """创建业务应用。"""
    repo = CmdbBusinessAppRepo(session)

    existing = await repo.get_by_app_code(payload.app_code)
    if existing is not None:
        raise ConflictError("CmdbBusinessApp", f"app_code '{payload.app_code}' already exists")

    app = CmdbBusinessApp(
        app_code=payload.app_code,
        name=payload.name,
        description=payload.description,
        team=payload.team,
        owner=payload.owner,
        department=payload.department,
        labels=payload.labels,
    )
    app = await repo.create(app)
    await session.commit()

    logger.info("CMDB business app created", extra={"app_code": payload.app_code})
    return app


async def update_app(
    session: AsyncSession, app_id: int, payload: BusinessAppUpdate,
) -> CmdbBusinessApp:
    """更新业务应用。"""
    repo = CmdbBusinessAppRepo(session)
    app = await repo.get_by_id(app_id)
    if app is None:
        raise NotFoundError("CmdbBusinessApp", str(app_id))

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(app, field, value)

    app = await repo.update(app)
    await session.commit()

    logger.info("CMDB business app updated", extra={"app_id": app_id})
    return app


async def delete_app(session: AsyncSession, app_id: int) -> None:
    """删除业务应用。"""
    repo = CmdbBusinessAppRepo(session)
    app = await repo.get_by_id(app_id)
    if app is None:
        raise NotFoundError("CmdbBusinessApp", str(app_id))

    await repo.delete(app)
    await session.commit()
    logger.info("CMDB business app deleted", extra={"app_id": app_id})
