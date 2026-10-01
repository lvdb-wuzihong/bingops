"""任务系统业务编排层（runbook 管理 + 执行编排 + 回滚触发）。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.core.config import settings
from bingops.core.exceptions import (
    ConflictError,
    ExternalServiceError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from bingops.models.cmdb.model import CmdbModel
from bingops.models.cmdb.resource import CmdbResource
from bingops.models.credential import CREDENTIAL_KINDS
from bingops.models.jobs import JobExecution, JobStep, Runbook
from bingops.models.ticket import Ticket
from bingops.models.user import User
from bingops.repositories.credential_repo import CredentialRepo
from bingops.repositories.job_gateway_repo import JobGatewayRepo
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
from bingops.services import (
    change_freeze_service,
    credential_service,
    gateway_service,
)
from bingops.services.credential_service import vault_ref_of
from bingops.tasks.jobs import dispatcher

logger = logging.getLogger(f"bingops.{__name__}")

# P3 审批挂接：达到该风险等级的 runbook 必须携带已审批通过的工单才可执行（超管除外）
APPROVAL_RISK_LEVELS = ("medium", "high", "critical")

# 目标机 IP 提取候选键（跨模型通用 code 优先）
_IP_FIELD_CANDIDATES = ("private_ip", "internal_ip", "ip")

# P1 默认目标范围：ansible 走 SSH，仅稳定可 SSH 的云主机；
# K8s 对象（P2 local 模式）与自建主机模型按需扩入
DEFAULT_TARGET_MODELS: list[str] = ["aliyun_ecs", "gcp_compute"]

# 唯一步骤的缺省超时（秒）
DEFAULT_STEP_TIMEOUT_SEC = 600

# 执行类型 → 默认执行位置（v29 扁平单步）：ansible/shell/script 跑在目标机上，
# python/terraform 在 runner 本机。
# shell 与 script 的分界是“入口是什么”而不是“在哪里跑”：shell 的 entry 是
# 内联命令（目标机 shell 直接执行），script 的 entry 是**仓库内的脚本文件**
# （runner 从 code_ref 拉仓库后用 ansible script 模块推送执行）——目标机
# 上并没有那个文件，拿 shell 去 `bash scripts/x.sh` 必失败。
EXEC_TYPE_RUN_ON: dict[str, str] = {
    "ansible": "target",
    "shell": "target",
    "script": "target",
    "python": "local",
    "terraform": "local",
}

RUN_ON_VALUES = ("target", "local")

# 单步模型的固定步骤 key（job_steps 行标识；v37 起一步一行，无第二次尝试）
SINGLE_STEP_KEY = "main"

# 入口指向仓库文件的执行类型：这些类型的 code_ref 必须固定版本。
# shell 的 entry 是内联命令，runner 不 clone 任何代码——给它一个 git tag 没有
# 意义（v38：修 v36 的一刀切，当时“跑一句 df -h”会被 code_ref 必填 400 卡住）
TYPES_WITH_REPO_CODE = ("ansible", "script", "python", "terraform")


def requires_code_ref(exec_type: str) -> bool:
    """该执行类型是否依赖仓库代码（决定 code_ref 是否必填）。"""
    return exec_type in TYPES_WITH_REPO_CODE


# runbooks 的步骤列（PUT 部分更新时与存量列合并后整体校验）
_STEP_COLUMNS = (
    "exec_type", "entry", "run_on", "timeout_sec",
)

# 定义类字段变更 → version +1（execution 快照语义）
# v36：default_target_resource_ids / default_code_ref 已删除——目标机与版本
# 是每次执行的核心决策，不得缓存到定义面
_DEFINITION_FIELDS = (
    *_STEP_COLUMNS, "params_schema", "secrets_schema", "connection", "target_models",
)

# 报错即文档：契约不满足时把最小可用示例直接回给作者
MINIMAL_RUNBOOK_HINT = (
    '{"name": "...", "exec_type": "python", "entry": "scripts/xxx.py",'
    ' "params_schema": {}, "secrets_schema": {"DB_PASSWORD": {"required": true}}}'
)


# ── Runbook 管理 ──────────────────────────────────────────────────────────────


def enabled_exec_types() -> set[str]:
    """平台允许创建/执行的执行类型白名单。

    上线顺序保险：runner 尚未支持某 executor 时收紧配置，
    平台侧即拒绝创建，而不是等任务下发后失败。
    """
    return {t.strip() for t in settings.job_step_types.split(",") if t.strip()}


def run_on_of(exec_type: str, run_on: str | None) -> str:
    """执行位置：显式 run_on 优先，否则按执行类型缺省。"""
    return run_on or EXEC_TYPE_RUN_ON.get(exec_type, "target")


def _build_step(
    exec_type: str, entry: str, run_on: str | None = None,
    timeout_sec: int | None = None,
) -> dict:
    """校验并归一出唯一步骤的列值（v29 扁平化：不再有 steps 数组）。

    返回的键与 runbooks 步骤列一一对应；execution 快照与 dispatch 消息都由这些列组装。
    entry 语义随 exec_type 显式区分（shell=内联命令 / script=仓库内脚本），不做隐式猜。
    v37：rollbackable 已删除——平台不提供回滚能力，也不注入 BINGOPS_ACTION=undo。
    """
    if exec_type not in EXEC_TYPE_RUN_ON:
        raise ValidationError(
            f"unknown exec_type '{exec_type}' (supported: {sorted(EXEC_TYPE_RUN_ON)})",
        )
    if exec_type not in enabled_exec_types():
        raise ValidationError(
            f"exec_type '{exec_type}' is not enabled "
            f"(BINGOPS_JOB_STEP_TYPES={sorted(enabled_exec_types())})",
        )
    if not isinstance(entry, str) or not entry.strip():
        raise ValidationError(f"entry is required; minimal example: {MINIMAL_RUNBOOK_HINT}")
    final_run_on = run_on_of(exec_type, run_on)
    if final_run_on not in RUN_ON_VALUES:
        raise ValidationError(f"run_on must be one of {list(RUN_ON_VALUES)}")
    return {
        "exec_type": exec_type,
        "entry": entry.strip(),
        "run_on": final_run_on,
        "timeout_sec": timeout_sec or DEFAULT_STEP_TIMEOUT_SEC,
    }


def step_of(runbook: Runbook) -> dict:
    """从 runbook 步骤列组装 dispatch / 快照用的唯一步骤对象。"""
    return {
        "key": SINGLE_STEP_KEY,
        "name": runbook.name,
        "type": runbook.exec_type,
        "run_on": runbook.run_on,
        "entry": runbook.entry,
        "timeout_sec": runbook.timeout_sec,
    }


def _validate_secrets_schema(secrets_schema: dict) -> None:
    """secrets_schema 条目形式校验（v32）。

    条目可选 `kind`：限定引用的凭据类型（如 db_password），前端据此过滤下拉候选；
    写错类型在创建时就拒，而不是拖到执行时才发现解不出。
    """
    for name, spec in (secrets_schema or {}).items():
        if not isinstance(spec, dict):
            continue
        kind = spec.get("kind")
        if kind is not None and kind not in CREDENTIAL_KINDS:
            raise ValidationError(
                f"secrets_schema '{name}': unknown kind '{kind}' "
                f"(supported: {list(CREDENTIAL_KINDS)})"
            )


async def _validate_secrets(
    session: AsyncSession, secrets_schema: dict, secrets: dict,
) -> dict:
    """secrets 校验 + 凭据目录解析（v32），返回可直接下发的 Vault 引用集。

    值可以是 `credentials.name`（推荐：下拉选、可反查影响面），也可以是裸
    Vault 路径（存量兼容）。条目声明了 `kind` 时强制走目录并校验类型匹配。
    平台不读 Vault，只把名字展开成路径；取真值仍是 runner 的唯一出口。
    """
    declared = secrets_schema or {}
    provided = dict(secrets or {})
    undeclared = sorted(set(provided) - set(declared))
    if undeclared:
        raise ValidationError(f"secrets not declared in runbook secrets_schema: {undeclared}")

    repo = CredentialRepo(session)
    out: dict[str, str] = {}
    for name, spec in declared.items():
        spec = spec if isinstance(spec, dict) else {}
        value = provided.get(name)
        if value is None:
            if spec.get("default_ref"):
                value = spec["default_ref"]
            elif spec.get("required"):
                raise ValidationError(f"missing required secret: {name}")
            else:
                continue
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(
                f"secret '{name}' must be a credential name or a Vault reference"
            )
        value = value.strip()
        credential = await repo.get_by_name(value)
        if credential is not None:
            want_kind = spec.get("kind")
            if want_kind and credential.kind != want_kind:
                raise ValidationError(
                    f"secret '{name}': credential '{value}' is kind "
                    f"'{credential.kind}', expected '{want_kind}'"
                )
            out[name] = vault_ref_of(credential)
        elif spec.get("kind"):
            raise ValidationError(
                f"secret '{name}': '{value}' is not in the credential catalog, "
                f"but this entry requires kind '{spec['kind']}'"
            )
        else:
            out[name] = value  # 存量兼容：直接当 Vault 路径透传
    return out


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
    _validate_secrets_schema(payload.secrets_schema)
    step = _build_step(
        payload.exec_type, payload.entry, run_on=payload.run_on,
        timeout_sec=payload.timeout_sec,
    )
    # v34：连接三件套已撤到执行面——runbook 不再持有任何连接信息，
    # 登录用户/密钥/提权全部在执行时提供
    runbook = Runbook(
        name=payload.name,
        category=payload.category,
        description=payload.description,
        params_schema=payload.params_schema,
        secrets_schema=payload.secrets_schema,
        target_models=payload.target_models or list(DEFAULT_TARGET_MODELS),
        risk_level=payload.risk_level,
        created_by=user.id,
        **step,
    )
    runbook = await RunbookRepo(session).create(runbook)
    await session.commit()
    logger.info("Runbook created", extra={"runbook_id": runbook.id, "runbook_name": runbook.name})
    return runbook


async def get_runbook(session: AsyncSession, runbook_id: int) -> Runbook:
    runbook = await RunbookRepo(session).get_by_id(runbook_id)
    if runbook is None:
        raise NotFoundError("Runbook", str(runbook_id))
    return runbook


async def update_runbook(session: AsyncSession, runbook_id: int, payload: RunbookUpdate) -> Runbook:
    runbook = await get_runbook(session, runbook_id)
    data = payload.model_dump(exclude_unset=True)
    if "secrets_schema" in data:
        _validate_secrets_schema(data["secrets_schema"])
    # 步骤列部分更新：与存量列合并后整体校验（只改 timeout 也不会破坏入口契约），
    # 并把归一后的缺省值回写 data（run_on 等推断值不落库为空）
    if any(k in data for k in _STEP_COLUMNS):
        merged = {k: data.get(k, getattr(runbook, k)) for k in _STEP_COLUMNS}
        data.update(_build_step(**merged))
    # v34：连接三件套已撤到执行面——runbook 编辑不再接受任何连接字段；
    # 存量行的 connection 列原样保留（执行时仍作兜底）
    # 定义类字段变更 → version +1（execution 快照语义）
    definition_changed = any(k in data for k in _DEFINITION_FIELDS)
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
    session: AsyncSession, resource_ids: list[int], gateway_name: str | None = None,
) -> list[ExecutionTarget]:
    """从 CMDB 生成目标快照（纯坐标 + 中转路径）。

    登录用户与凭据不属于目标——它们在执行面解析（v34），由本函数的调用方
    统一填进 targets，保证同一执行内身份一致。
    """
    result = await session.execute(
        select(CmdbResource, CmdbModel.code)
        .join(CmdbModel, CmdbResource.model_id == CmdbModel.id)
        .where(CmdbResource.id.in_(resource_ids), CmdbResource.deleted_at.is_(None))
    )
    rows = {res.id: (res, code) for res, code in result.all()}
    missing = [rid for rid in resource_ids if rid not in rows]
    if missing:
        raise NotFoundError("CmdbResource", str(missing))

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

    # 中转路径：未指定网关时按机器所属 VPC 自动选路（v35 单一维度）
    active_gateways = await JobGatewayRepo(session).list_active()
    forced_gateway = (
        await gateway_service.gateway_payload_by_name(session, gateway_name)
        if gateway_name else None
    )

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
        host = {"vpc_id": fields.get("vpc_id")}
        gateway_payload = forced_gateway if forced_gateway is not None else (
            await gateway_service.gateway_payload_for_host(session, host, active_gateways)
        )
        targets.append(ExecutionTarget(
            resource_id=res.id, name=res.name, ip=ip, region=res.region, model_code=code,
            cluster_id=res.cloud_account if is_k8s else None,
            namespace=namespace,
            gateway=gateway_payload,
        ))
    return targets


def _build_dispatch(execution: JobExecution, step: dict | None = None) -> JobDispatchMessage:
    return JobDispatchMessage(
        message_id=str(uuid.uuid4()),
        execution_id=execution.id,
        code_ref=execution.code_ref,
        params=execution.params,
        # 只带钥匙名；真钥匙由 runner 在 executor 之前统一解析并加入 redact
        secrets=execution.secrets,
        connection=execution.connection,
        targets=[ExecutionTarget(**t) for t in execution.target_resources],
        # v29：单步对象；v37：不再有“重发快照 + 注入 undo”的回滚路径
        step=DispatchStep(**(step if step is not None else execution.step_snapshot)),
    )


async def _send_dispatch(
    execution: JobExecution, step: dict | None = None,
) -> None:
    try:
        await dispatcher.send_dispatch(_build_dispatch(execution, step))
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
    needs_targets = runbook.run_on == "target"
    params = _validate_params(runbook.params_schema, payload.params)
    secrets = await _validate_secrets(session, runbook.secrets_schema, payload.secrets)
    # 连接三件套（v34）：登录用户/钥匙/提权在执行面提供。
    # 优先级：执行时填写 > runbook.connection（存量兜底）；目标型任务缺任一即 400，
    # 报错直接说清缺哪样——跨用户是常态，身份与钥匙都不属于任务定义
    login_user = ssh_key_ref = None
    if needs_targets:
        login_user, ssh_key_ref = await credential_service.resolve_execution_credentials(
            session, runbook.connection, payload.ssh_user, payload.ssh_credential,
        )
    become = (
        bool(payload.become) if payload.become is not None
        else bool((runbook.connection or {}).get("become"))
    )

    # P3 高危门控：中高危必须挂已审批工单（先于目标快照，提前拒绝）
    await _check_approval_gate(session, runbook, payload.ticket_id, user)

    # 目标机（v36）：必须由每次执行显式选择，不再从 runbook 继承“默认目标”。
    # 目标锁/封禁/审批/审计全以目标机为对象，预选项会把最危险的一步变成“不假思索”；
    # 要省点击就做「复用上次的目标机」（数据源是 job_executions 快照，不会腐烂）。
    # 继承来的目标照走 running / target_models 白名单 / 并发锁硬校验——简化的是
    # 填写量，不是安全边界
    resource_ids = list(payload.target_resource_ids or [])
    if needs_targets and not resource_ids:
        raise ValidationError(
            "target_resource_ids is required: 目标机必须由每次执行显式选择"
            "（前端可用「复用上次的目标机」从执行历史带入）"
        )
    targets = (
        await _snapshot_targets(session, resource_ids, gateway_name=payload.gateway_name)
        if resource_ids else []
    )
    # 登录身份与钥匙是执行级事实（v34）：同一执行内逐台同值，
    # 由 runner 的 target 优先级直接消费；audit 也从 targets 就能看清“以谁连哪台”
    for t in targets:
        t.ssh_user = login_user
        t.ssh_key_ref = ssh_key_ref

    # P3 封禁窗口门控：命中全局/模型范围封禁即拒绝（含工单自动下发路径）。
    # v27 无目标任务已核：scope 为空的全局封禁对空 model_codes 照样命中，不会绕过
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

    # 版本要求按类型判定（v38）：入口在仓库里的类型必须固定版本（不给“默认 main”、
    # 也不在 runbook 上缓存）；shell 的内联命令不 clone 代码，code_ref 留空即合法，
    # runner 据此跳过仓库拉取
    code_ref = (payload.code_ref or settings.job_default_code_ref or "").strip()
    if requires_code_ref(runbook.exec_type) and not code_ref:
        raise ValidationError(
            f"code_ref is required for exec_type={runbook.exec_type}: 入口指向仓库文件，"
            "必须固定 git 版本；未显式传且平台未配置 BINGOPS_JOB_DEFAULT_CODE_REF"
            "（前端可用「复用上次的版本」带入）"
        )

    execution = JobExecution(
        runbook_id=runbook.id,
        runbook_version=runbook.version,
        code_ref=code_ref,
        params=params,
        secrets=secrets,
        target_resources=[t.model_dump() for t in targets],
        step_snapshot=step_of(runbook),
        # connection 快照 = 存量兜底 + 本次执行解析出的身份/钥匙/提权：
        # runner 只读这一份，不再二次猜测
        connection={
            **(runbook.connection or {}),
            **({"ssh_user": login_user, "ssh_key_ref": ssh_key_ref}
               if needs_targets else {}),
            "become": become,
        },
        # v37：rollback_policy 列已删——不存在回滚，也无自动/手动之分
        ticket_id=payload.ticket_id,
        triggered_by=user.id,
    )
    execution = await JobExecutionRepo(session).create(execution)
    await session.commit()  # 先提交再下发：防止 runner 事件回流时 execution 行尚未落库

    try:
        await _send_dispatch(execution)
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
        raise NotFoundError("Execution", str(execution_id))
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
    if execution.status not in ("pending", "running"):
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


# ── 回滚能力已于 v37 整体下线 ────────────────────────────────────────────
# 删除的入参/字段：runbooks.rollbackable、job_executions.rollback_policy、
# job_steps.attempt_type、dispatch.command、POST /executions/{id}/rollback。
# 理由：runner 从未实现 undo，契约里留着会让人以为“失败可以一键撤销”；
# 内联命令还会产生命令假成功。失败就落 failed，由人看日志修。
# 将来解冻的正确形态：先定“撤销什么、谁审批、失败如何可见”，而不是复活这几个字段。


# ── 步骤与日志查询 ────────────────────────────────────────────────────────────


async def list_steps(session: AsyncSession, execution_id: int) -> list[JobStep]:
    return await JobStepRepo(session).list_by_execution(execution_id)


async def get_step(session: AsyncSession, step_id: int) -> JobStep:
    from bingops.models.jobs import JobStep as _JobStep

    result = await session.execute(select(_JobStep).where(_JobStep.id == step_id))
    step = result.scalar_one_or_none()
    if step is None:
        raise NotFoundError("JobStep", str(step_id))
    return step


async def list_step_logs(
    session: AsyncSession, step_id: int, after_seq: int = 0,
) -> list:
    await get_step(session, step_id)
    return await JobStepLogRepo(session).list_after(step_id, after_seq)
