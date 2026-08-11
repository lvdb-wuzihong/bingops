"""CMDB 同步任务数据访问层。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.models.cmdb.sync_task import CmdbSyncTask


class CmdbSyncTaskRepo:
    """CMDB 同步任务 Repository。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, task_id: int) -> CmdbSyncTask | None:
        result = await self._session.execute(
            select(CmdbSyncTask).where(CmdbSyncTask.id == task_id)
        )
        return result.scalar_one_or_none()

    async def get_by_type_and_target(
        self, task_type: str, target_id: str,
    ) -> CmdbSyncTask | None:
        """根据任务类型 + 目标标识查询（唯一约束）。"""
        result = await self._session.execute(
            select(CmdbSyncTask).where(
                CmdbSyncTask.task_type == task_type,
                CmdbSyncTask.target_id == target_id,
            )
        )
        return result.scalar_one_or_none()

    async def is_enabled(self, task_type: str, target_id: str) -> bool:
        """判断指定同步任务是否启用（消费端快速查询）。"""
        result = await self._session.execute(
            select(CmdbSyncTask.enabled).where(
                CmdbSyncTask.task_type == task_type,
                CmdbSyncTask.target_id == target_id,
            )
        )
        enabled = result.scalar_one_or_none()
        # 未配置任务时默认放行（兼容未配置场景）
        return enabled if enabled is not None else True

    async def list_tasks(
        self,
        *,
        task_type: str | None = None,
        enabled: bool | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[CmdbSyncTask], int]:
        """分页查询同步任务列表。"""
        query = select(CmdbSyncTask)
        count_query = select(CmdbSyncTask.id)

        if task_type:
            query = query.where(CmdbSyncTask.task_type == task_type)
            count_query = count_query.where(CmdbSyncTask.task_type == task_type)
        if enabled is not None:
            query = query.where(CmdbSyncTask.enabled == enabled)
            count_query = count_query.where(CmdbSyncTask.enabled == enabled)
        if keyword:
            like_pattern = f"%{keyword}%"
            query = query.where(CmdbSyncTask.name.ilike(like_pattern))
            count_query = count_query.where(CmdbSyncTask.name.ilike(like_pattern))

        total_result = await self._session.execute(
            select(func.count()).select_from(count_query.subquery())
        )
        total = total_result.scalar() or 0

        query = query.order_by(CmdbSyncTask.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)
        result = await self._session.execute(query)
        tasks = list(result.scalars().all())

        return tasks, total

    async def create(self, task: CmdbSyncTask) -> CmdbSyncTask:
        self._session.add(task)
        await self._session.flush()
        return task

    async def update(self, task: CmdbSyncTask) -> CmdbSyncTask:
        await self._session.flush()
        return task

    async def delete(self, task: CmdbSyncTask) -> None:
        await self._session.delete(task)
        await self._session.flush()
