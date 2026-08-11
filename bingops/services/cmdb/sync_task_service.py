"""CMDB 同步任务业务服务。"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from bingops.core.exceptions import ConflictError, NotFoundError, ValidationError
from bingops.models.cmdb.sync_task import CmdbSyncTask
from bingops.repositories.cmdb.sync_task_repo import CmdbSyncTaskRepo
from bingops.schemas.cmdb.sync_task import SyncTaskCreate, SyncTaskUpdate

logger = logging.getLogger(f"bingops.{__name__}")

VALID_TASK_TYPES = ("k8s", "cloud")


async def list_sync_tasks(
    session: AsyncSession,
    *,
    task_type: str | None = None,
    enabled: bool | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[CmdbSyncTask], int]:
    """分页查询同步任务列表。"""
    repo = CmdbSyncTaskRepo(session)
    return await repo.list_tasks(
        task_type=task_type,
        enabled=enabled,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )


async def get_sync_task(session: AsyncSession, task_id: int) -> CmdbSyncTask:
    """获取同步任务详情。"""
    repo = CmdbSyncTaskRepo(session)
    task = await repo.get_by_id(task_id)
    if task is None:
        raise NotFoundError("CmdbSyncTask", str(task_id))
    return task


async def create_sync_task(session: AsyncSession, payload: SyncTaskCreate) -> CmdbSyncTask:
    """创建同步任务。"""
    # 校验 task_type
    if payload.task_type not in VALID_TASK_TYPES:
        raise ValidationError(f"task_type must be one of: {VALID_TASK_TYPES}")

    # cloud 类型必须指定 provider
    if payload.task_type == "cloud" and not payload.provider:
        raise ValidationError("provider is required for cloud sync task")

    repo = CmdbSyncTaskRepo(session)

    # 唯一约束检查
    existing = await repo.get_by_type_and_target(payload.task_type, payload.target_id)
    if existing is not None:
        raise ConflictError(
            "CmdbSyncTask",
            f"sync task already exists: {payload.task_type}/{payload.target_id}",
        )

    task = CmdbSyncTask(
        name=payload.name,
        task_type=payload.task_type,
        provider=payload.provider,
        target_id=payload.target_id,
        resource_types=payload.resource_types,
        schedule=payload.schedule,
        enabled=payload.enabled,
        description=payload.description,
    )
    task = await repo.create(task)
    await session.commit()

    logger.info(
        "Sync task created",
        extra={"task_id": task.id, "task_type": payload.task_type, "target_id": payload.target_id},
    )
    return task


async def update_sync_task(
    session: AsyncSession, task_id: int, payload: SyncTaskUpdate,
) -> CmdbSyncTask:
    """更新同步任务。"""
    repo = CmdbSyncTaskRepo(session)
    task = await repo.get_by_id(task_id)
    if task is None:
        raise NotFoundError("CmdbSyncTask", str(task_id))

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(task, field, value)

    task = await repo.update(task)
    await session.commit()

    logger.info("Sync task updated", extra={"task_id": task_id})
    return task


async def delete_sync_task(session: AsyncSession, task_id: int) -> None:
    """删除同步任务。"""
    repo = CmdbSyncTaskRepo(session)
    task = await repo.get_by_id(task_id)
    if task is None:
        raise NotFoundError("CmdbSyncTask", str(task_id))

    await repo.delete(task)
    await session.commit()

    logger.info("Sync task deleted", extra={"task_id": task_id})


async def toggle_sync_task(session: AsyncSession, task_id: int, enabled: bool) -> CmdbSyncTask:
    """启用/禁用同步任务。"""
    repo = CmdbSyncTaskRepo(session)
    task = await repo.get_by_id(task_id)
    if task is None:
        raise NotFoundError("CmdbSyncTask", str(task_id))

    task.enabled = enabled
    task = await repo.update(task)
    await session.commit()

    logger.info(
        "Sync task toggled",
        extra={"task_id": task_id, "enabled": enabled},
    )
    return task


async def is_sync_enabled(session: AsyncSession, task_type: str, target_id: str) -> bool:
    """判断同步任务是否启用（供消费端调用）。"""
    repo = CmdbSyncTaskRepo(session)
    return await repo.is_enabled(task_type, target_id)
