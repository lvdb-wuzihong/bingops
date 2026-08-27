"""任务系统 job-events 消费处理器（runner → bingops）。

事件类型：
- step_started：建/更新步骤行为 running
- log：追加步骤日志行（(step_id, seq) 冲突跳过，防重放重复）
- step_finished：步骤终态；do 步骤失败推进 execution（auto 策略触发自动回滚）
- execution_finished：execution 终态落定（runner 为终态裁决者）
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bingops.models.jobs import JobStep
from bingops.repositories.jobs_repo import JobExecutionRepo, JobStepLogRepo, JobStepRepo
from bingops.schemas.jobs import JobEventMessage
from bingops.services import job_service

logger = logging.getLogger(f"bingops.{__name__}")

# message_id 去重（Kafka at-least-once 重放去噪）
DEDUP_CAP = 5000
_message_dedup: OrderedDict[str, None] = OrderedDict()


def _is_duplicate(message_id: str) -> bool:
    if message_id in _message_dedup:
        return True
    _message_dedup[message_id] = None
    while len(_message_dedup) > DEDUP_CAP:
        _message_dedup.popitem(last=False)
    return False


def create_job_event_handler(session_factory: async_sessionmaker[AsyncSession]):
    """创建 job-events 处理函数（闭包注入 session_factory）。"""

    async def handle_job_event(message: JobEventMessage) -> None:
        if _is_duplicate(message.message_id):
            logger.debug("Duplicate job event, skipping", extra={"message_id": message.message_id})
            return
        async with session_factory() as session:
            try:
                await _process(session, message)
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception(
                    "Failed to process job event",
                    extra={
                        "execution_id": message.execution_id,
                        "event_type": message.event_type,
                        "step_key": message.step_key,
                    },
                )

    return handle_job_event


# ── 事件分发 ──────────────────────────────────────────────────────────────────


async def _process(session: AsyncSession, message: JobEventMessage) -> None:
    execution = await JobExecutionRepo(session).get_by_id(message.execution_id)
    if execution is None:
        logger.warning(
            "Job event for unknown execution",
            extra={"execution_id": message.execution_id},
        )
        return

    if message.event_type == "execution_finished":
        await _finalize_execution(session, execution, message)
        return

    if not message.step_key:
        logger.warning("Job event without step_key", extra={"execution_id": message.execution_id})
        return

    step_repo = JobStepRepo(session)
    step = await step_repo.get_by_key(
        message.execution_id, message.step_key, message.attempt_type,
    )

    if message.event_type == "step_started":
        if step is None:
            step = await step_repo.create(_new_step(execution, message, status="running"))
        else:
            step.status = "running"
            step.started_at = datetime.now(timezone.utc)
            await step_repo.update(step)
        return

    if message.event_type == "log":
        if step is None:
            # 日志先于 started 到达（重放乱序）：补行
            step = await step_repo.create(_new_step(execution, message, status="running"))
        if message.seq is not None and message.line is not None:
            await JobStepLogRepo(session).append(
                step.id, message.seq, message.level, message.host, message.line,
            )
        return

    if message.event_type == "step_finished":
        now = datetime.now(timezone.utc)
        if step is None:
            step = _new_step(execution, message, status=message.status or "failed")
            step.finished_at = now
            step = await step_repo.create(step)
        else:
            step.status = message.status or "failed"
            step.exit_code = message.exit_code
            step.error_message = message.error
            step.finished_at = now
            await step_repo.update(step)

        if step.status == "failed" and message.attempt_type == "do":
            await _handle_do_step_failure(session, execution)


# ── 状态推进 ──────────────────────────────────────────────────────────────────


def _new_step(execution, message: JobEventMessage, status: str) -> JobStep:
    """按快照补建步骤行（step_name/serial 取自 steps_snapshot）。"""
    snapshot = next(
        (s for s in (execution.steps_snapshot or []) if s.get("key") == message.step_key),
        {},
    )
    return JobStep(
        execution_id=execution.id,
        step_key=message.step_key or "",
        step_name=snapshot.get("name"),
        type=snapshot.get("type", "ansible"),
        attempt_type=message.attempt_type,
        status=status,
        serial=snapshot.get("serial"),
        started_at=datetime.now(timezone.utc),
    )


async def _handle_do_step_failure(session: AsyncSession, execution) -> None:
    """do 步骤失败：auto 策略触发逆序回滚，manual 策略落 failed 等人工。

    自动回滚守卫：没有任何 do 步骤成功过（如 dispatch 契约校验失败）
    则无可回滚对象，直接落 failed，避免“没执行却回滚中”的语义荒谬。
    """
    if execution.status != "running":
        return
    has_done = await JobStepRepo(session).has_succeeded_do_step(execution.id)
    if execution.rollback_policy == "auto" and has_done:
        execution.status = "failed"
        await JobExecutionRepo(session).update(execution)
        await job_service.trigger_rollback(session, execution)
    else:
        execution.status = "failed"
        execution.finished_at = datetime.now(timezone.utc)
        await JobExecutionRepo(session).update(execution)
        logger.info(
            "Job execution failed (manual rollback policy)",
            extra={"execution_id": execution.id},
        )


async def _finalize_execution(session: AsyncSession, execution, message: JobEventMessage) -> None:
    """execution 终态落定（守卫非法跃迁，防迟到消息覆盖人工操作）。"""
    now = datetime.now(timezone.utc)
    final = message.status or "failed"
    allowed = {
        "success": {"running"},
        "failed": {"running", "pending"},
        "rolled_back": {"rolling_back"},
        "partial_rollback": {"rolling_back"},
        "rollback_failed": {"rolling_back"},
    }
    if execution.status not in allowed.get(final, set()):
        logger.debug(
            "Execution finished event ignored (illegal transition)",
            extra={"execution_id": execution.id, "current": execution.status, "final": final},
        )
        return
    execution.status = final
    execution.finished_at = now
    await JobExecutionRepo(session).update(execution)
    logger.info("Job execution finished", extra={"execution_id": execution.id, "status": final})
