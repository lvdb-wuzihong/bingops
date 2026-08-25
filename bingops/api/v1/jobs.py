"""任务系统 API 路由（runbook 管理 + 执行编排 + 步骤日志）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.api.dependencies import get_db_session, require_permission
from bingops.core.response import paginated_response, success_response
from bingops.models.jobs import JobExecution, JobStep, JobStepLog, Runbook
from bingops.models.user import User
from bingops.schemas.jobs import (
    ExecutionCreate,
    ExecutionDetailResponse,
    ExecutionResponse,
    RunbookCreate,
    RunbookResponse,
    RunbookUpdate,
    StepLogResponse,
    StepResponse,
)
from bingops.services import job_service

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


# ── 响应转换 ──────────────────────────────────────────────────────────────────


def _runbook_to_response(runbook: Runbook) -> dict:
    return RunbookResponse(
        id=runbook.id,
        name=runbook.name,
        category=runbook.category,
        description=runbook.description,
        params_schema=runbook.params_schema or {},
        steps=runbook.steps or [],
        connection=runbook.connection or {},
        version=runbook.version,
        risk_level=runbook.risk_level,
        auto_rollback=runbook.auto_rollback,
        is_active=runbook.is_active,
        created_by=runbook.created_by,
        created_at=runbook.created_at,
        updated_at=runbook.updated_at,
    ).model_dump(mode="json")


def _execution_to_response(execution: JobExecution) -> dict:
    return ExecutionResponse(
        id=execution.id,
        runbook_id=execution.runbook_id,
        runbook_version=execution.runbook_version,
        code_ref=execution.code_ref,
        params=execution.params or {},
        target_resources=execution.target_resources or [],
        connection=execution.connection or {},
        status=execution.status,
        rollback_policy=execution.rollback_policy,
        ticket_id=execution.ticket_id,
        triggered_by=execution.triggered_by,
        started_at=execution.started_at,
        finished_at=execution.finished_at,
        created_at=execution.created_at,
        updated_at=execution.updated_at,
    ).model_dump(mode="json")


def _step_to_response(step: JobStep) -> dict:
    return StepResponse(
        id=step.id,
        execution_id=step.execution_id,
        step_key=step.step_key,
        step_name=step.step_name,
        type=step.type,
        attempt_type=step.attempt_type,
        status=step.status,
        serial=step.serial,
        exit_code=step.exit_code,
        error_message=step.error_message,
        started_at=step.started_at,
        finished_at=step.finished_at,
    ).model_dump(mode="json")


def _log_to_response(log: JobStepLog) -> dict:
    return StepLogResponse(
        id=log.id,
        step_id=log.step_id,
        seq=log.seq,
        level=log.level,
        host=log.host,
        line=log.line,
        logged_at=log.logged_at,
    ).model_dump(mode="json")


# ── Runbook ───────────────────────────────────────────────────────────────────


@router.get("/runbooks")
async def list_runbooks(
    keyword: str | None = None,
    category: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("runbook:list"),
):
    """查询 runbook 列表（分页）。"""
    runbooks, total = await job_service.list_runbooks(
        session, keyword=keyword, category=category, page=page, page_size=page_size,
    )
    items = [_runbook_to_response(r) for r in runbooks]
    return paginated_response(items, total, page, page_size)


@router.post("/runbooks", status_code=201)
async def create_runbook(
    payload: RunbookCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = require_permission("runbook:create"),
):
    """创建 runbook。"""
    runbook = await job_service.create_runbook(session, payload, current_user)
    return success_response(
        data=_runbook_to_response(runbook), message="Runbook created", http_status=201,
    )


@router.get("/runbooks/{runbook_id}")
async def get_runbook(
    runbook_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("runbook:get"),
):
    """获取 runbook 详情。"""
    runbook = await job_service.get_runbook(session, runbook_id)
    return success_response(data=_runbook_to_response(runbook))


@router.put("/runbooks/{runbook_id}")
async def update_runbook(
    runbook_id: int,
    payload: RunbookUpdate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("runbook:update"),
):
    """更新 runbook（steps/params_schema/connection 变更 → version +1）。"""
    runbook = await job_service.update_runbook(session, runbook_id, payload)
    return success_response(data=_runbook_to_response(runbook), message="Runbook updated")


@router.delete("/runbooks/{runbook_id}")
async def delete_runbook(
    runbook_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("runbook:delete"),
):
    """删除 runbook（有执行历史时拒绝，改用 is_active=false 下线）。"""
    await job_service.delete_runbook(session, runbook_id)
    return success_response(message="Runbook deleted")


# ── Execution ─────────────────────────────────────────────────────────────────


@router.get("/executions")
async def list_executions(
    status: str | None = None,
    runbook_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("job:list"),
):
    """查询执行实例列表（分页）。"""
    executions, total = await job_service.list_executions(
        session, status=status, runbook_id=runbook_id, page=page, page_size=page_size,
    )
    items = [_execution_to_response(e) for e in executions]
    return paginated_response(items, total, page, page_size)


@router.post("/executions", status_code=201)
async def create_execution(
    payload: ExecutionCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = require_permission("job:create"),
):
    """创建并下发执行（目标/步骤/版本三快照 + 并发目标锁）。"""
    execution = await job_service.create_execution(session, payload, current_user)
    return success_response(
        data=_execution_to_response(execution), message="Job dispatched", http_status=201,
    )


@router.get("/executions/{execution_id}")
async def get_execution(
    execution_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("job:get"),
):
    """获取执行详情（含步骤列表）。"""
    execution = await job_service.get_execution(session, execution_id)
    steps = await job_service.list_steps(session, execution_id)
    detail = ExecutionDetailResponse(
        **_execution_to_response(execution),
        steps=[StepResponse(**_step_to_response(s)) for s in steps],
    )
    return success_response(data=detail.model_dump(mode="json"))


@router.post("/executions/{execution_id}/cancel")
async def cancel_execution(
    execution_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("job:cancel"),
):
    """取消执行（仅 pending/running）。"""
    execution = await job_service.cancel_execution(session, execution_id)
    return success_response(data=_execution_to_response(execution), message="Job cancelled")


@router.post("/executions/{execution_id}/rollback")
async def rollback_execution(
    execution_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("job:rollback"),
):
    """手动触发回滚（逆序重跑已完成且 rollbackable 的步骤 undo 分支）。"""
    execution = await job_service.rollback_execution(session, execution_id)
    return success_response(data=_execution_to_response(execution), message="Rollback dispatched")


# ── 步骤日志 ──────────────────────────────────────────────────────────────────


@router.get("/steps/{step_id}/logs")
async def list_step_logs(
    step_id: int,
    after_seq: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("job:get"),
):
    """拉取步骤日志（after_seq 增量拉取，前端轮询 live tail）。"""
    logs = await job_service.list_step_logs(session, step_id, after_seq)
    items = [_log_to_response(log) for log in logs]
    return success_response(data=items)
