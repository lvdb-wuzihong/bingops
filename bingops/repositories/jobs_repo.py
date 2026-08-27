"""任务系统数据访问层。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.models.jobs import JobExecution, JobStep, JobStepLog, Runbook

# 执行中的状态集合（并发目标锁校验用）
ACTIVE_EXECUTION_STATUSES = ("pending", "running", "rolling_back")


class RunbookRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, runbook: Runbook) -> Runbook:
        self.session.add(runbook)
        await self.session.flush()
        return runbook

    async def update(self, runbook: Runbook) -> Runbook:
        await self.session.flush()
        return runbook

    async def get_by_id(self, runbook_id: int) -> Runbook | None:
        result = await self.session.execute(
            select(Runbook).where(Runbook.id == runbook_id)
        )
        return result.scalar_one_or_none()

    async def delete(self, runbook: Runbook) -> None:
        await self.session.delete(runbook)
        await self.session.flush()

    async def list(
        self,
        keyword: str | None = None,
        category: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Runbook], int]:
        query = select(Runbook)
        if keyword:
            query = query.where(Runbook.name.ilike(f"%{keyword}%"))
        if category:
            query = query.where(Runbook.category == category)

        total = (
            await self.session.execute(select(func.count()).select_from(query.subquery()))
        ).scalar() or 0

        query = query.order_by(Runbook.id.desc()).offset((page - 1) * page_size).limit(page_size)
        result = await self.session.execute(query)
        return list(result.scalars().all()), total


class JobExecutionRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, execution: JobExecution) -> JobExecution:
        self.session.add(execution)
        await self.session.flush()
        return execution

    async def update(self, execution: JobExecution) -> JobExecution:
        await self.session.flush()
        return execution

    async def get_by_id(self, execution_id: int) -> JobExecution | None:
        result = await self.session.execute(
            select(JobExecution).where(JobExecution.id == execution_id)
        )
        return result.scalar_one_or_none()

    async def list(
        self,
        status: str | None = None,
        runbook_id: int | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[JobExecution], int]:
        query = select(JobExecution)
        if status:
            query = query.where(JobExecution.status == status)
        if runbook_id:
            query = query.where(JobExecution.runbook_id == runbook_id)

        total = (
            await self.session.execute(select(func.count()).select_from(query.subquery()))
        ).scalar() or 0

        query = (
            query.order_by(JobExecution.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all()), total

    async def list_active(self) -> list[JobExecution]:
        """执行中的 execution（并发目标锁校验用）。"""
        result = await self.session.execute(
            select(JobExecution).where(JobExecution.status.in_(ACTIVE_EXECUTION_STATUSES))
        )
        return list(result.scalars().all())

    async def has_any(self, runbook_id: int) -> bool:
        result = await self.session.execute(
            select(func.count())
            .select_from(JobExecution)
            .where(JobExecution.runbook_id == runbook_id)
        )
        return (result.scalar() or 0) > 0

    async def get_latest_by_ticket(self, ticket_id: int) -> JobExecution | None:
        """按工单查询最新一次执行（P3 工单↔任务闭环回显用）。"""
        result = await self.session.execute(
            select(JobExecution)
            .where(JobExecution.ticket_id == ticket_id)
            .order_by(JobExecution.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


class JobStepRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, step: JobStep) -> JobStep:
        self.session.add(step)
        await self.session.flush()
        return step

    async def update(self, step: JobStep) -> JobStep:
        await self.session.flush()
        return step

    async def get_by_key(
        self, execution_id: int, step_key: str, attempt_type: str = "do",
    ) -> JobStep | None:
        result = await self.session.execute(
            select(JobStep).where(
                JobStep.execution_id == execution_id,
                JobStep.step_key == step_key,
                JobStep.attempt_type == attempt_type,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_execution(self, execution_id: int) -> list[JobStep]:
        result = await self.session.execute(
            select(JobStep)
            .where(JobStep.execution_id == execution_id)
            .order_by(JobStep.id.asc())
        )
        return list(result.scalars().all())

    async def has_succeeded_do_step(self, execution_id: int) -> bool:
        """是否存在成功完成的 do 步骤（自动回滚守卫：无成功步骤则无可回滚对象）。"""
        result = await self.session.execute(
            select(func.count())
            .select_from(JobStep)
            .where(
                JobStep.execution_id == execution_id,
                JobStep.attempt_type == "do",
                JobStep.status == "success",
            )
        )
        return (result.scalar() or 0) > 0


class JobStepLogRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def append(
        self,
        step_id: int,
        seq: int,
        level: str,
        host: str | None,
        line: str,
    ) -> None:
        """追加日志行（(step_id, seq) 冲突时跳过，防重放重复）。"""
        stmt = (
            insert(JobStepLog)
            .values(step_id=step_id, seq=seq, level=level, host=host, line=line)
            .on_conflict_do_nothing(constraint="uq_job_step_log_seq")
        )
        await self.session.execute(stmt)

    async def list_after(
        self, step_id: int, after_seq: int = 0, limit: int = 500,
    ) -> list[JobStepLog]:
        result = await self.session.execute(
            select(JobStepLog)
            .where(JobStepLog.step_id == step_id, JobStepLog.seq > after_seq)
            .order_by(JobStepLog.seq.asc())
            .limit(limit)
        )
        return list(result.scalars().all())
