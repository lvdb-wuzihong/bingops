"""任务系统业务编排层（runbook 管理 + 执行编排 + 回滚触发）。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.core.exceptions import (
    ConflictError,
    ExternalServiceError,
    NotFoundError,
    ValidationError,
)
from bingops.models.cmdb.model import CmdbModel
from bingops.models.cmdb.resource import CmdbResource
from bingops.models.jobs import JobExecution, JobStep, Runbook
from bingops.models.user import User
from bingops.repositories.jobs_repo import (
    JobExecutionRepo,
    JobStepLogRepo,
    JobStepRepo,
    RunbookRepo,
)
from bingops.schemas.jobs import (
    DispatchStep,
    ExecutionCreate,
    ExecutionTarget,
    JobDispatchMessage,
    RunbookCreate,
    RunbookUpdate,
)
from bingops.tasks.jobs import dispatcher

logger = logging.getLogger(f"bingops.{__name__}")

# 目标机 IP 提取候选键（跨模型通用 code 优先）
_IP_FIELD_CANDIDATES = ("private_ip", "internal_ip", "ip")

# P1 默认目标范围：ansible 走 SSH，仅稳定可 SSH 的云主机；
# K8s 对象（P2 local 模式）与自建主机模型按需扩入
DEFAULT_TARGET_MODELS: list[str] = ["aliyun_ecs", "gcp_compute"]

ROLLBACKABLE_SOURCE_STATUSES = ("failed",)


# ── Runbook 管理 ──────────────────────────────────────────────────────────────


def _validate_steps(steps: list[dict]) -> list[dict]:
    """步骤契约校验（P1 仅 ansible）：key/playbook 必备、key 唯一。"""
    seen: set[str] = set()
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValidationError(f"steps[{i}] must be an object")
        key = step.get("key")
        if not key or not isinstance(key, str):
            raise ValidationError(f"steps[{i}].key is required")
        if key in seen:
            raise ValidationError(f"duplicate step key: {key}")
        seen.add(key)
        step_type = step.get("type", "ansible")
        if step_type != "ansible":
            raise ValidationError(
                f"step '{key}': unsupported type '{step_type}' (P1: ansible only)",
            )
        if not step.get("playbook"):
            raise ValidationError(f"step '{key}': playbook is required")
    return steps


def _validate_connection(connection: dict) -> None:
    """connection 契约校验（P1 ssh 模式）：ssh_key_ref 必备，真钥匙在 Vault。

    在 bingops 侧 fail-fast（422），不把契约校验责任推给 runner。
    """
    ref = (connection or {}).get("ssh_key_ref")
    if not isinstance(ref, str) or not ref.strip():
        raise ValidationError("runbook connection.ssh_key_ref is required (Vault key name)")


def _validate_params(params_schema: dict, params: dict) -> None:
    """按 params_schema 做 required 存在性 + 基础类型校验。"""
    for name, spec in (params_schema or {}).items():
        required = bool(spec.get("required")) if isinstance(spec, dict) else False
        value = params.get(name)
        if value is None:
            if required:
                raise ValidationError(f"missing required param: {name}")
            continue
        ptype = spec.get("type") if isinstance(spec, dict) else None
        ok = (
            ptype is None
            or (ptype == "string" and isinstance(value, str))
            or (ptype == "number"
                and isinstance(value, (int, float)) and not isinstance(value, bool))
            or (ptype == "boolean" and isinstance(value, bool))
        )
        if not ok:
            raise ValidationError(f"param '{name}' type mismatch, expected {ptype}")


async def list_runbooks(
    session: AsyncSession,
    keyword: str | None = None,
    category: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Runbook], int]:
    return await RunbookRepo(session).list(keyword, category, page, page_size)


async def create_runbook(session: AsyncSession, payload: RunbookCreate, user: User) -> Runbook:
    _validate_steps(payload.steps)
    _validate_connection(payload.connection)
    runbook = Runbook(
        name=payload.name,
        category=payload.category,
        description=payload.description,
        params_schema=payload.params_schema,
        steps=payload.steps,
        connection=payload.connection,
        target_models=payload.target_models or list(DEFAULT_TARGET_MODELS),
        risk_level=payload.risk_level,
        auto_rollback=payload.auto_rollback,
        created_by=user.id,
    )
    runbook = await RunbookRepo(session).create(runbook)
    await session.commit()
    logger.info("Runbook created", extra={"runbook_id": runbook.id, "runbook_name": runbook.name})
    return runbook


async def get_runbook(session: AsyncSession, runbook_id: int) -> Runbook:
    runbook = await RunbookRepo(session).get_by_id(runbook_id)
    if runbook is None:
        raise NotFoundError(f"Runbook {runbook_id} not found")
    return runbook


async def update_runbook(session: AsyncSession, runbook_id: int, payload: RunbookUpdate) -> Runbook:
    runbook = await get_runbook(session, runbook_id)
    data = payload.model_dump(exclude_unset=True)
    if "steps" in data:
        _validate_steps(data["steps"])
    if "connection" in data:
        _validate_connection(data["connection"])
    # 定义类字段变更 → version +1（execution 快照语义）
    definition_changed = any(
        k in data for k in ("steps", "params_schema", "connection", "target_models")
    )
    for key, value in data.items():
        setattr(runbook, key, value)
    if definition_changed:
        runbook.version += 1
    await RunbookRepo(session).update(runbook)
    await session.commit()
    logger.info(
        "Runbook updated",
        extra={"runbook_id": runbook.id, "version": runbook.version, "changed": sorted(data)},
    )
    return runbook


async def delete_runbook(session: AsyncSession, runbook_id: int) -> None:
    runbook = await get_runbook(session, runbook_id)
    if await JobExecutionRepo(session).has_any(runbook_id):
        raise ConflictError("Runbook", "has execution history, deactivate instead of delete")
    await RunbookRepo(session).delete(runbook)
    await session.commit()
    logger.info("Runbook deleted", extra={"runbook_id": runbook_id})


# ── 执行编排 ──────────────────────────────────────────────────────────────────


async def _snapshot_targets(
    session: AsyncSession, resource_ids: list[int],
) -> list[ExecutionTarget]:
    """从 CMDB 生成目标快照（只带资源坐标，不带 secret）。"""
    result = await session.execute(
        select(CmdbResource, CmdbModel.code)
        .join(CmdbModel, CmdbResource.model_id == CmdbModel.id)
        .where(CmdbResource.id.in_(resource_ids), CmdbResource.deleted_at.is_(None))
    )
    rows = {res.id: (res, code) for res, code in result.all()}
    missing = [rid for rid in resource_ids if rid not in rows]
    if missing:
        raise NotFoundError(f"Target resources not found: {missing}")

    targets = []
    for rid in resource_ids:
        res, code = rows[rid]
        fields = res.fields or {}
        ip = next((fields.get(k) for k in _IP_FIELD_CANDIDATES if fields.get(k)), None)
        is_k8s = code.startswith("k8s_")
        namespace = None
        if is_k8s:
            # provider_id 格式 {cluster}/{ns}/{name}（namespace 级）或 {cluster}/{name}
            parts = (res.provider_id or "").split("/")
            namespace = parts[1] if len(parts) >= 3 else None
        targets.append(ExecutionTarget(
            resource_id=res.id, name=res.name, ip=ip, region=res.region, model_code=code,
            cluster_id=res.cloud_account if is_k8s else None,
            namespace=namespace,
        ))
    return targets


def _build_dispatch(execution: JobExecution, command: str) -> JobDispatchMessage:
    return JobDispatchMessage(
        message_id=str(uuid.uuid4()),
        command=command,
        execution_id=execution.id,
        code_ref=execution.code_ref,
        params=execution.params,
        connection=execution.connection,
        targets=[ExecutionTarget(**t) for t in execution.target_resources],
        steps=[DispatchStep(**s) for s in execution.steps_snapshot],
    )


async def _send_dispatch(execution: JobExecution, command: str) -> None:
    try:
        await dispatcher.send_dispatch(_build_dispatch(execution, command))
    except RuntimeError as exc:
        # Kafka 未启用/未注入：下发通道不可用，503 语义（配置类失败而非外部故障）
        raise ExternalServiceError("kafka", str(exc), http_status=503) from exc


async def create_execution(
    session: AsyncSession, payload: ExecutionCreate, user: User,
) -> JobExecution:
    runbook = await get_runbook(session, payload.runbook_id)
    if not runbook.is_active:
        raise ConflictError("Runbook", f"runbook {runbook.id} is deactivated")
    # 存量 runbook 可能创建於校验上线前：下发前再验一次 connection 契约
    _validate_connection(runbook.connection)
    _validate_params(runbook.params_schema, payload.params)

    targets = await _snapshot_targets(session, payload.target_resource_ids)

    # 目标模型范围硬校验：runbook 声明 scope 之外的资源一律拒绝
    allowed = set(runbook.target_models or DEFAULT_TARGET_MODELS)
    bad = sorted({t.model_code for t in targets if t.model_code not in allowed})
    if bad:
        raise ValidationError(
            f"Targets outside runbook scope {bad}: allowed={sorted(allowed)}",
        )

    # 并发目标锁：与执行中的 execution 目标交集命中即拒绝
    active = await JobExecutionRepo(session).list_active()
    wanted = {t.resource_id for t in targets}
    for exe in active:
        overlap = wanted & {t.get("resource_id") for t in (exe.target_resources or [])}
        if overlap:
            raise ConflictError(
                "JobExecution",
                f"targets {sorted(overlap)} are busy in execution {exe.id} "
                f"(status={exe.status})",
            )

    execution = JobExecution(
        runbook_id=runbook.id,
        runbook_version=runbook.version,
        code_ref=payload.code_ref,
        params=payload.params,
        target_resources=[t.model_dump() for t in targets],
        steps_snapshot=list(runbook.steps),
        connection=runbook.connection,
        rollback_policy="auto" if runbook.auto_rollback else "manual",
        triggered_by=user.id,
    )
    execution = await JobExecutionRepo(session).create(execution)
    await session.commit()  # 先提交再下发：防止 runner 事件回流时 execution 行尚未落库

    try:
        await _send_dispatch(execution, "execute")
    except Exception:
        # 下发失败（如 Kafka 未启用）：置 failed 释放目标锁，避免 pending 残留
        execution.status = "failed"
        execution.finished_at = datetime.now(timezone.utc)
        await JobExecutionRepo(session).update(execution)
        await session.commit()
        raise
    execution.status = "running"
    execution.started_at = datetime.now(timezone.utc)
    await JobExecutionRepo(session).update(execution)
    await session.commit()

    logger.info(
        "Job execution dispatched",
        extra={"execution_id": execution.id, "runbook_id": runbook.id, "targets": len(targets)},
    )
    return execution


async def get_execution(session: AsyncSession, execution_id: int) -> JobExecution:
    execution = await JobExecutionRepo(session).get_by_id(execution_id)
    if execution is None:
        raise NotFoundError(f"Execution {execution_id} not found")
    return execution


async def list_executions(
    session: AsyncSession,
    status: str | None = None,
    runbook_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[JobExecution], int]:
    return await JobExecutionRepo(session).list(status, runbook_id, page, page_size)


async def cancel_execution(session: AsyncSession, execution_id: int) -> JobExecution:
    execution = await get_execution(session, execution_id)
    if execution.status not in ("pending", "running", "rolling_back"):
        raise ConflictError(
            "JobExecution",
            f"execution {execution_id} cannot be cancelled (status={execution.status})",
        )
    execution.status = "cancelled"
    execution.finished_at = datetime.now(timezone.utc)
    await JobExecutionRepo(session).update(execution)
    await session.commit()
    logger.info("Job execution cancelled", extra={"execution_id": execution_id})
    return execution


async def trigger_rollback(session: AsyncSession, execution: JobExecution) -> JobExecution:
    """触发回滚下发（手动 API 与自动回滚共用）。"""
    if execution.status not in ROLLBACKABLE_SOURCE_STATUSES:
        raise ConflictError(
            "JobExecution",
            f"execution {execution.id} cannot rollback (status={execution.status})",
        )
    rollbackable = [s.get("key") for s in execution.steps_snapshot if s.get("rollbackable")]
    if not rollbackable:
        raise ConflictError("JobExecution", "no rollbackable steps in this execution")

    await _send_dispatch(execution, "rollback")
    execution.status = "rolling_back"
    await JobExecutionRepo(session).update(execution)
    await session.commit()
    logger.info(
        "Job rollback dispatched",
        extra={"execution_id": execution.id, "rollbackable_steps": rollbackable},
    )
    return execution


async def rollback_execution(session: AsyncSession, execution_id: int) -> JobExecution:
    execution = await get_execution(session, execution_id)
    return await trigger_rollback(session, execution)


# ── 步骤与日志查询 ────────────────────────────────────────────────────────────


async def list_steps(session: AsyncSession, execution_id: int) -> list[JobStep]:
    return await JobStepRepo(session).list_by_execution(execution_id)


async def get_step(session: AsyncSession, step_id: int) -> JobStep:
    from bingops.models.jobs import JobStep as _JobStep

    result = await session.execute(select(_JobStep).where(_JobStep.id == step_id))
    step = result.scalar_one_or_none()
    if step is None:
        raise NotFoundError(f"Step {step_id} not found")
    return step


async def list_step_logs(
    session: AsyncSession, step_id: int, after_seq: int = 0,
) -> list:
    await get_step(session, step_id)
    return await JobStepLogRepo(session).list_after(step_id, after_seq)
