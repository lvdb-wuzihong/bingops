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
    PermissionDeniedError,
    ValidationError,
)
from bingops.models.cmdb.model import CmdbModel
from bingops.models.cmdb.resource import CmdbResource
from bingops.models.jobs import JobExecution, JobStep, Runbook
from bingops.models.ticket import Ticket
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
from bingops.services import change_freeze_service
from bingops.tasks.jobs import dispatcher

logger = logging.getLogger(f"bingops.{__name__}")

# P3 审批挂接：达到该风险等级的 runbook 必须携带已审批通过的工单才可执行（超管除外）
APPROVAL_RISK_LEVELS = ("medium", "high", "critical")

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


def _validate_params(params_schema: dict, params: dict) -> dict:
    """按 params_schema 校验（required/type/enum）并回填 default。

    条目 spec 支持：type(string|number|boolean)/required/default/enum/description。
    返回归一化后的 params（含默认值），调用方须用返回值落库/下发——
    前端动态表单只需收集用户实际填写项，缺省由后端补齐。
    """
    normalized = dict(params or {})
    for name, spec in (params_schema or {}).items():
        if not isinstance(spec, dict):
            continue
        value = normalized.get(name)
        if value is None:
            if "default" in spec:
                normalized[name] = spec["default"]
                continue
            if spec.get("required"):
                raise ValidationError(f"missing required param: {name}")
            continue
        ptype = spec.get("type")
        ok = (
            ptype is None
            or (ptype == "string" and isinstance(value, str))
            or (ptype == "number"
                and isinstance(value, (int, float)) and not isinstance(value, bool))
            or (ptype == "boolean" and isinstance(value, bool))
        )
        if not ok:
            raise ValidationError(f"param '{name}' type mismatch, expected {ptype}")
        enum = spec.get("enum")
        if isinstance(enum, list) and value not in enum:
            raise ValidationError(f"param '{name}' must be one of: {enum}")
    return normalized


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

    # 执行态硬校验：仅 running 可作为执行目标。
    # stopped SSH 必失败、maintenance 变更中；unknown/NULL 按 fail-safe 从严拒绝。
    not_ready = sorted(
        (res.name, res.status or "null")
        for res, _code in rows.values()
        if res.status != "running"
    )
    if not_ready:
        detail = ", ".join(f"{name}({status})" for name, status in not_ready)
        raise ValidationError(f"targets not in running state: {detail}")

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


def _build_dispatch(
    execution: JobExecution, command: str, steps: list[dict] | None = None,
) -> JobDispatchMessage:
    return JobDispatchMessage(
        message_id=str(uuid.uuid4()),
        command=command,
        execution_id=execution.id,
        code_ref=execution.code_ref,
        params=execution.params,
        connection=execution.connection,
        targets=[ExecutionTarget(**t) for t in execution.target_resources],
        steps=[
            DispatchStep(**s)
            for s in (steps if steps is not None else execution.steps_snapshot)
        ],
    )


async def _send_dispatch(
    execution: JobExecution, command: str, steps: list[dict] | None = None,
) -> None:
    try:
        await dispatcher.send_dispatch(_build_dispatch(execution, command, steps))
    except RuntimeError as exc:
        # Kafka 未启用/未注入：下发通道不可用，503 语义（配置类失败而非外部故障）
        raise ExternalServiceError("kafka", str(exc), http_status=503) from exc


async def _check_approval_gate(
    session: AsyncSession, runbook: Runbook, ticket_id: int | None, user: User,
) -> None:
    """高危门控：中高危 runbook 必须携带已审批通过且 runbook 匹配的工单。"""
    if runbook.risk_level not in APPROVAL_RISK_LEVELS or user.is_superuser:
        return
    if ticket_id is None:
        raise PermissionDeniedError(
            f"runbook risk_level={runbook.risk_level} requires an approved ticket",
        )
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        raise NotFoundError("Ticket", str(ticket_id))
    if ticket.approval_status != "approved":
        raise PermissionDeniedError(f"ticket {ticket_id} is not approved")
    if ticket.runbook_id != runbook.id:
        raise ValidationError(f"ticket {ticket_id} is not attached to runbook {runbook.id}")


async def create_execution(
    session: AsyncSession, payload: ExecutionCreate, user: User,
) -> JobExecution:
    runbook = await get_runbook(session, payload.runbook_id)
    if not runbook.is_active:
        raise ConflictError("Runbook", f"runbook {runbook.id} is deactivated")
    # 存量 runbook 可能创建於校验上线前：下发前再验一次 connection 契约
    _validate_connection(runbook.connection)
    params = _validate_params(runbook.params_schema, payload.params)

    # P3 高危门控：中高危必须挂已审批工单（先于目标快照，提前拒绝）
    await _check_approval_gate(session, runbook, payload.ticket_id, user)

    targets = await _snapshot_targets(session, payload.target_resource_ids)

    # P3 封禁窗口门控：命中全局/模型范围封禁即拒绝（含工单自动下发路径）
    model_codes = {t.model_code for t in targets if t.model_code}
    freezes = await change_freeze_service.find_active_freezes_for_models(session, model_codes)
    if freezes:
        names = ", ".join(f.name for f in freezes)
        raise ConflictError("JobExecution", f"change freeze active: {names}")

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
        params=params,
        target_resources=[t.model_dump() for t in targets],
        steps_snapshot=list(runbook.steps),
        connection=runbook.connection,
        rollback_policy="auto" if runbook.auto_rollback else "manual",
        ticket_id=payload.ticket_id,
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


async def _completed_rollbackable_steps(
    session: AsyncSession, execution: JobExecution,
) -> list[dict]:
    """回滚链过滤：已完成（do 步骤 status=success）且声明 rollbackable 的步骤。

    runner 无状态，不知道哪些步骤跑过——控制面按 job_steps 过滤后下发；
    未执行（失败步之后的 skipped）与失败步本身不进回滚链。
    """
    done = {
        s.step_key
        for s in await JobStepRepo(session).list_by_execution(execution.id)
        if s.attempt_type == "do" and s.status == "success"
    }
    return [
        s for s in execution.steps_snapshot or []
        if s.get("rollbackable") and s.get("key") in done
    ]


async def trigger_rollback(session: AsyncSession, execution: JobExecution) -> JobExecution:
    """触发回滚下发（手动 API 与自动回滚共用）。"""
    if execution.status not in ROLLBACKABLE_SOURCE_STATUSES:
        raise ConflictError(
            "JobExecution",
            f"execution {execution.id} cannot rollback (status={execution.status})",
        )
    # 回滚链过滤（runner 无状态，控制面给什么跑什么）：仅已完成且 rollbackable 的步骤
    rollback_steps = await _completed_rollbackable_steps(session, execution)
    if not rollback_steps:
        raise ConflictError(
            "JobExecution", "no completed rollbackable steps in this execution",
        )

    await _send_dispatch(execution, "rollback", steps=rollback_steps)
    execution.status = "rolling_back"
    await JobExecutionRepo(session).update(execution)
    await session.commit()
    logger.info(
        "Job rollback dispatched",
        extra={
            "execution_id": execution.id,
            "rollback_steps": [s.get("key") for s in rollback_steps],
        },
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
