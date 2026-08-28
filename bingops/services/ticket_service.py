"""工单系统业务服务。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from bingops.models.cmdb.change_log import CmdbChangeLog
from bingops.models.cmdb.model import CmdbModel
from bingops.models.cmdb.resource import CmdbResource
from bingops.models.cmdb.tag import CmdbResourceTag
from bingops.models.jobs import Runbook
from bingops.models.ticket import (
    OncallSchedule,
    Ticket,
    TicketApproval,
    TicketCatalog,
    TicketComment,
    TicketGroup,
)
from bingops.models.user import User
from bingops.repositories.jobs_repo import JobExecutionRepo
from bingops.repositories.ticket_meta_repo import (
    OncallScheduleRepo,
    TicketCatalogRepo,
    TicketGroupRepo,
)
from bingops.repositories.ticket_repo import (
    ChangeFreezeRepo,
    TicketApprovalRepo,
    TicketCommentRepo,
    TicketRepo,
)
from bingops.repositories.user_repo import UserRepo
from bingops.schemas.jobs import ExecutionCreate
from bingops.schemas.ticket import ChangeContextResource, TicketCreate, TicketUpdate
from bingops.services import job_service

logger = logging.getLogger(f"bingops.{__name__}")

VALID_TICKET_TYPES = ("general", "request", "change", "incident")
VALID_PRIORITIES = ("low", "medium", "high", "urgent")

# P3 审批挂接：风险阈值见 job_service.APPROVAL_RISK_LEVELS（低危自动直通）
VALID_APPROVAL_ACTIONS = ("approve", "reject")

# 状态流转矩阵：当前状态 → 允许的目标状态（pending_approval 为审批门禁态）
STATUS_TRANSITIONS: dict[str, set[str]] = {
    "pending_approval": {"open", "cancelled"},
    "open": {"in_progress", "cancelled"},
    "in_progress": {"resolved", "cancelled"},
    "resolved": {"closed", "in_progress"},
    "closed": set(),
    "cancelled": set(),
}

# 终态：不允许评论与任何变更
TERMINAL_STATUSES = ("closed", "cancelled")


async def _get_ticket_or_fail(session: AsyncSession, ticket_id: int) -> Ticket:
    """获取工单，不存在则抛出 NotFoundError。"""
    repo = TicketRepo(session)
    ticket = await repo.get_by_id(ticket_id)
    if ticket is None:
        raise NotFoundError("Ticket", str(ticket_id))
    return ticket


async def _get_user_or_fail(session: AsyncSession, user_id: int) -> User:
    """获取用户，不存在则抛出 NotFoundError。"""
    user = await UserRepo(session).get_by_id(user_id)
    if user is None:
        raise NotFoundError("User", str(user_id))
    return user


def _can_manage(ticket: Ticket, operator: User) -> bool:
    """判断操作人是否为创建人或超级管理员。"""
    return operator.is_superuser or ticket.creator_id == operator.id


async def list_tickets(
    session: AsyncSession,
    *,
    status: str | None = None,
    ticket_type: str | None = None,
    priority: str | None = None,
    creator_id: int | None = None,
    assignee_id: int | None = None,
    group_id: int | None = None,
    catalog_item_id: int | None = None,
    target_resource_id: int | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Ticket], int]:
    """分页查询工单列表。"""
    repo = TicketRepo(session)
    return await repo.list_tickets(
        status=status,
        ticket_type=ticket_type,
        priority=priority,
        creator_id=creator_id,
        assignee_id=assignee_id,
        group_id=group_id,
        catalog_item_id=catalog_item_id,
        target_resource_id=target_resource_id,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )


async def get_ticket(session: AsyncSession, ticket_id: int) -> Ticket:
    """获取工单详情。"""
    return await _get_ticket_or_fail(session, ticket_id)


async def list_comments(session: AsyncSession, ticket_id: int) -> list[TicketComment]:
    """获取工单流转记录。"""
    await _get_ticket_or_fail(session, ticket_id)
    return await TicketCommentRepo(session).list_by_ticket(ticket_id)


async def list_approvals(session: AsyncSession, ticket_id: int) -> list[TicketApproval]:
    """获取工单审批记录。"""
    await _get_ticket_or_fail(session, ticket_id)
    return await TicketApprovalRepo(session).list_by_ticket(ticket_id)


def _extract_target_ids(payload: TicketCreate) -> list[int]:
    """提取执行目标 ID：target_resource_ids 优先，兼容 job_params/related_resource_id 旧路径。"""
    if payload.target_resource_ids:
        return list(payload.target_resource_ids)
    raw = payload.job_params.get("target_resource_ids")
    if isinstance(raw, list) and raw:
        if not all(isinstance(x, int) for x in raw):
            raise ValidationError("job_params.target_resource_ids must be a list of int")
        return list(raw)
    if payload.related_resource_id is not None:
        return [payload.related_resource_id]
    return []


async def _validate_runbook_intent(session: AsyncSession, payload: TicketCreate) -> Runbook:
    """校验变更工单携带的执行意图（runbook 存在且启用、目标非空、参数合法）。"""
    result = await session.execute(select(Runbook).where(Runbook.id == payload.runbook_id))
    runbook = result.scalar_one_or_none()
    if runbook is None:
        raise NotFoundError("Runbook", str(payload.runbook_id))
    if not runbook.is_active:
        raise ConflictError("Runbook", f"runbook {runbook.id} is deactivated")
    if not payload.code_ref:
        raise ValidationError("code_ref (git tag) is required when runbook_id is set")
    if not _extract_target_ids(payload):
        raise ValidationError(
            "target_resource_ids (in job_params) or related_resource_id is required",
        )
    job_service._validate_params(runbook.params_schema, payload.job_params.get("params", {}))
    return runbook


async def create_ticket(session: AsyncSession, payload: TicketCreate, operator: User) -> Ticket:
    """创建工单并生成工单号；携带 runbook 时按风险等级决定审批策略。"""
    if payload.assignee_id is not None:
        await _get_user_or_fail(session, payload.assignee_id)
    if payload.related_resource_id is not None:
        resource = await session.get(CmdbResource, payload.related_resource_id)
        if resource is None:
            raise NotFoundError("CmdbResource", str(payload.related_resource_id))

    # 服务目录：校验二级事项 + 快照难度 + 派生默认类型/预绑 runbook
    catalog_item: TicketCatalog | None = None
    if payload.catalog_item_id is not None:
        catalog_item = await TicketCatalogRepo(session).get_by_id(payload.catalog_item_id)
        if catalog_item is None:
            raise NotFoundError("TicketCatalog", str(payload.catalog_item_id))
        if not catalog_item.is_active:
            raise ValidationError(f"catalog item {catalog_item.id} is deactivated")
        if catalog_item.parent_id is None:
            raise ValidationError("catalog_item_id must reference a level-2 item")

    group: TicketGroup | None = None
    if payload.group_id is not None:
        group = await TicketGroupRepo(session).get_by_id(payload.group_id)
        if group is None:
            raise NotFoundError("TicketGroup", str(payload.group_id))
        if not group.is_active:
            raise ValidationError(f"group {group.id} is deactivated")

    ticket_type = payload.ticket_type
    if catalog_item is not None and "ticket_type" not in payload.model_fields_set:
        ticket_type = catalog_item.default_type
    if ticket_type not in VALID_TICKET_TYPES:
        raise ValidationError(f"ticket_type must be one of: {VALID_TICKET_TYPES}")
    if payload.priority not in VALID_PRIORITIES:
        raise ValidationError(f"priority must be one of: {VALID_PRIORITIES}")

    # P3 执行意图：runbook 校验 + 风险分级决定审批策略（低危自动直通）
    runbook: Runbook | None = None
    effective_runbook_id = payload.runbook_id or (
        catalog_item.default_runbook_id if catalog_item is not None else None
    )
    if effective_runbook_id is not None:
        payload.runbook_id = effective_runbook_id
        runbook = await _validate_runbook_intent(session, payload)

    # 值班自动派单：未显式指派且带处理组时，按当日值班 tier1 轮转
    assignee_id = payload.assignee_id
    auto_assigned = False
    if assignee_id is None and group is not None:
        assignee_id, auto_assigned = await _resolve_oncall_assignee(session, group.id)

    needs_approval = (
        runbook is not None and runbook.risk_level in job_service.APPROVAL_RISK_LEVELS
    )

    repo = TicketRepo(session)
    ticket = Ticket(
        ticket_no="",  # flush 后基于主键回填
        title=payload.title,
        description=payload.description,
        ticket_type=ticket_type,
        status="pending_approval" if needs_approval else "open",
        priority=payload.priority,
        creator_id=operator.id,
        assignee_id=assignee_id,
        related_resource_id=payload.related_resource_id,
        runbook_id=payload.runbook_id,
        job_params=payload.job_params,
        code_ref=payload.code_ref,
        approval_status="pending" if needs_approval else "none",
        catalog_item_id=payload.catalog_item_id,
        group_id=payload.group_id,
        difficulty=catalog_item.difficulty if catalog_item is not None else None,
        target_resource_ids=_extract_target_ids(payload),
    )
    ticket = await repo.create(ticket)
    ticket.ticket_no = f"TK-{ticket.id:08d}"
    await repo.update(ticket)

    await TicketCommentRepo(session).create(
        TicketComment(
            ticket_id=ticket.id,
            user_id=operator.id,
            action="create",
            content=payload.description,
        )
    )
    if auto_assigned and assignee_id is not None:
        await TicketCommentRepo(session).create(
            TicketComment(
                ticket_id=ticket.id,
                user_id=operator.id,
                action="assign",
                content="[auto-oncall]",
                to_value=str(assignee_id),
            )
        )
    await session.commit()

    logger.info(
        "Ticket created",
        extra={
            "ticket_id": ticket.id,
            "ticket_no": ticket.ticket_no,
            "user_id": operator.id,
            "approval_status": ticket.approval_status,
        },
    )

    # 低危变更自动直通：创建即下发（填单即执行）
    if runbook is not None and not needs_approval:
        await _dispatch_attached_job(session, ticket, operator)

    return ticket


async def _resolve_oncall_assignee(
    session: AsyncSession, group_id: int,
) -> tuple[int | None, bool]:
    """按当日值班表 tier1 轮转选取处理人（复刻多维表格自动赋值自动化）。"""
    today = datetime.now(timezone.utc).date()
    oncall: OncallSchedule | None = await OncallScheduleRepo(session).get_by_group_and_date(
        group_id, today,
    )
    if oncall is None or not oncall.tier1:
        return None, False

    day_start = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
    count_result = await session.execute(
        select(func.count())
        .select_from(Ticket)
        .where(Ticket.group_id == group_id, Ticket.created_at >= day_start)
    )
    today_count = count_result.scalar() or 0
    tier1 = [uid for uid in oncall.tier1 if isinstance(uid, int)]
    if not tier1:
        return None, False
    return tier1[today_count % len(tier1)], True


async def _dispatch_attached_job(
    session: AsyncSession, ticket: Ticket, operator: User,
) -> None:
    """按工单携带的执行意图创建并下发 job（封禁/并发锁由 job_service 统一门控）。

    下发失败不回滚工单：工单保留审计链路，异常透传给调用方。
    """
    target_ids = _extract_target_ids_from_ticket(ticket)
    payload = ExecutionCreate(
        runbook_id=ticket.runbook_id,
        params=ticket.job_params.get("params", {}),
        target_resource_ids=target_ids,
        code_ref=ticket.code_ref or "",
        ticket_id=ticket.id,
    )
    try:
        await job_service.create_execution(session, payload, operator)
    except Exception as exc:
        await TicketCommentRepo(session).create(
            TicketComment(
                ticket_id=ticket.id,
                user_id=operator.id,
                action="comment",
                content=f"[dispatch-failed] {exc.message if hasattr(exc, 'message') else exc}",
            )
        )
        await session.commit()
        raise

    logger.info(
        "Ticket job auto-dispatched",
        extra={"ticket_id": ticket.id, "runbook_id": ticket.runbook_id},
    )


def _extract_target_ids_from_ticket(ticket: Ticket) -> list[int]:
    """从已落库工单提取执行目标（一等列优先，兼容旧路径）。"""
    if ticket.target_resource_ids:
        return list(ticket.target_resource_ids)
    raw = (ticket.job_params or {}).get("target_resource_ids")
    if isinstance(raw, list) and raw:
        return list(raw)
    if ticket.related_resource_id is not None:
        return [ticket.related_resource_id]
    return []


async def submit_approval(
    session: AsyncSession, ticket_id: int, action: str, approver: User,
    comment: str | None = None,
) -> Ticket:
    """提交审批：通过 → 工单转 open（携带 runbook 时自动下发）；拒绝 → cancelled。"""
    if action not in VALID_APPROVAL_ACTIONS:
        raise ValidationError(f"action must be one of: {VALID_APPROVAL_ACTIONS}")

    ticket = await _get_ticket_or_fail(session, ticket_id)

    if ticket.approval_status != "pending":
        raise ValidationError("Ticket is not awaiting approval")
    if approver.id == ticket.creator_id and not approver.is_superuser:
        raise PermissionDeniedError("Creator cannot approve their own ticket")

    now = datetime.now(timezone.utc)
    approved = action == "approve"
    ticket.approval_status = "approved" if approved else "rejected"
    ticket.status = "open" if approved else "cancelled"
    if not approved:
        ticket.closed_at = now

    ticket = await TicketRepo(session).update(ticket)
    await TicketApprovalRepo(session).create(
        TicketApproval(
            ticket_id=ticket.id,
            approver_id=approver.id,
            action=action,
            comment=comment,
        )
    )
    await TicketCommentRepo(session).create(
        TicketComment(
            ticket_id=ticket.id,
            user_id=approver.id,
            action="status_change",
            content=comment,
            from_value="pending_approval",
            to_value=ticket.status,
        )
    )
    await session.commit()

    logger.info(
        "Ticket approval submitted",
        extra={"ticket_id": ticket_id, "action": action, "approver_id": approver.id},
    )

    # 审批通过且携带执行意图 → 自动下发（封禁窗口拦截在此生效）
    if approved and ticket.runbook_id is not None:
        await _dispatch_attached_job(session, ticket, approver)

    return ticket


async def update_ticket(
    session: AsyncSession, ticket_id: int, payload: TicketUpdate, operator: User,
) -> Ticket:
    """更新工单（仅 open 状态、创建人或管理员可操作）。"""
    ticket = await _get_ticket_or_fail(session, ticket_id)

    if ticket.status != "open":
        raise ValidationError("Ticket can only be updated in open status")
    if not _can_manage(ticket, operator):
        raise PermissionDeniedError("Only the creator or admin can update the ticket")
    if payload.priority is not None and payload.priority not in VALID_PRIORITIES:
        raise ValidationError(f"priority must be one of: {VALID_PRIORITIES}")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(ticket, field, value)

    ticket = await TicketRepo(session).update(ticket)
    await session.commit()

    logger.info("Ticket updated", extra={"ticket_id": ticket_id, "user_id": operator.id})
    return ticket


async def assign_ticket(
    session: AsyncSession, ticket_id: int, assignee_id: int, operator: User,
) -> Ticket:
    """指派/转派工单处理人。"""
    ticket = await _get_ticket_or_fail(session, ticket_id)

    if ticket.status not in ("open", "in_progress"):
        raise ValidationError("Ticket can only be assigned in open or in_progress status")

    assignee = await _get_user_or_fail(session, assignee_id)
    if not assignee.is_active:
        raise ValidationError("Assignee account is disabled")

    previous = ticket.assignee_id
    ticket.assignee_id = assignee_id
    ticket = await TicketRepo(session).update(ticket)

    await TicketCommentRepo(session).create(
        TicketComment(
            ticket_id=ticket.id,
            user_id=operator.id,
            action="assign",
            from_value=str(previous) if previous is not None else None,
            to_value=str(assignee_id),
        )
    )
    await session.commit()

    logger.info(
        "Ticket assigned",
        extra={"ticket_id": ticket_id, "assignee_id": assignee_id, "user_id": operator.id},
    )
    return ticket


async def change_ticket_status(
    session: AsyncSession, ticket_id: int, target_status: str, operator: User,
    comment: str | None = None,
) -> Ticket:
    """按状态流转矩阵推进工单状态。"""
    ticket = await _get_ticket_or_fail(session, ticket_id)

    if ticket.approval_status == "pending":
        raise ValidationError("Ticket is awaiting approval, use the approval endpoint")

    allowed = STATUS_TRANSITIONS.get(ticket.status, set())
    if target_status not in allowed:
        raise ValidationError(
            f"Invalid status transition: {ticket.status} -> {target_status}"
        )

    now = datetime.now(timezone.utc)
    previous = ticket.status
    ticket.status = target_status
    if target_status == "in_progress" and ticket.started_at is None:
        ticket.started_at = now
    if target_status == "resolved":
        ticket.resolved_at = now
    elif target_status == "closed":
        ticket.closed_at = now
    elif target_status == "in_progress" and previous == "resolved":
        # 重开时清空解决时间
        ticket.resolved_at = None

    ticket = await TicketRepo(session).update(ticket)

    await TicketCommentRepo(session).create(
        TicketComment(
            ticket_id=ticket.id,
            user_id=operator.id,
            action="status_change",
            content=comment,
            from_value=previous,
            to_value=target_status,
        )
    )
    await session.commit()

    logger.info(
        "Ticket status changed",
        extra={
            "ticket_id": ticket_id,
            "from_status": previous,
            "to_status": target_status,
            "user_id": operator.id,
        },
    )
    return ticket


async def add_comment(
    session: AsyncSession, ticket_id: int, content: str, operator: User,
) -> TicketComment:
    """添加工单评论（终态工单禁止评论）。"""
    ticket = await _get_ticket_or_fail(session, ticket_id)

    if ticket.status in TERMINAL_STATUSES:
        raise ValidationError("Cannot comment on a closed or cancelled ticket")

    comment = await TicketCommentRepo(session).create(
        TicketComment(
            ticket_id=ticket.id,
            user_id=operator.id,
            action="comment",
            content=content,
        )
    )
    await session.commit()

    logger.info("Ticket comment added", extra={"ticket_id": ticket_id, "user_id": operator.id})
    return comment


async def delete_ticket(session: AsyncSession, ticket_id: int, operator: User) -> None:
    """删除工单（仅 open 状态、创建人或管理员可操作）。"""
    ticket = await _get_ticket_or_fail(session, ticket_id)

    if ticket.status != "open":
        raise ValidationError("Only open tickets can be deleted")
    if not _can_manage(ticket, operator):
        raise PermissionDeniedError("Only the creator or admin can delete the ticket")

    await TicketRepo(session).delete(ticket)
    await session.commit()

    logger.info("Ticket deleted", extra={"ticket_id": ticket_id, "user_id": operator.id})


# ── 变更上下文聚合（判断变更时点与落地，P3） ─────────────────────────────

# 环境解析约定：env 事实源即运维标签（云资源 env / K8s k8s:env），manual 优先、cloud 兜底；
# 无标签时返回 None，由门控侧按 fail-safe 处理（默认从严视为 production）
ENV_FAILSAFE_DEFAULT = "production"
CHANGE_CONTEXT_WINDOW_DAYS = 7
CHANGE_CONTEXT_MAX_CHANGES = 5


def resolve_env(tags: list[CmdbResourceTag], *, is_k8s: bool) -> str | None:
    """按标签列表解析环境（K8s 读 k8s:env，云资源读 env；manual 优先）。"""
    key = "k8s:env" if is_k8s else "env"
    matched = [t for t in tags if t.tag_key == key]
    for source in ("manual", "cloud"):
        for tag in matched:
            if tag.source == source:
                return tag.tag_value
    return None


async def change_context(
    session: AsyncSession, resource_ids: list[int],
) -> list[ChangeContextResource]:
    """聚合目标资源的变更上下文：近期变更/占用任务/封禁窗口/环境。

    供变更工单创建与审批时判断变更时点是否合适。
    """
    if not resource_ids:
        return []

    # 1. 资源 + 模型 code
    result = await session.execute(
        select(CmdbResource, CmdbModel.code)
        .join(CmdbModel, CmdbResource.model_id == CmdbModel.id)
        .where(CmdbResource.id.in_(resource_ids), CmdbResource.deleted_at.is_(None))
    )
    rows = {res.id: (res, code) for res, code in result.all()}
    missing = [rid for rid in resource_ids if rid not in rows]
    if missing:
        raise NotFoundError("CmdbResource", str(missing))

    # 2. 环境标签（env / k8s:env）
    tag_result = await session.execute(
        select(CmdbResourceTag).where(
            CmdbResourceTag.resource_id.in_(resource_ids),
            CmdbResourceTag.tag_key.in_(["env", "k8s:env"]),
        )
    )
    tags_by_resource: dict[int, list[CmdbResourceTag]] = {}
    for tag in tag_result.scalars().all():
        tags_by_resource.setdefault(tag.resource_id, []).append(tag)

    # 3. 最近 7 天变更记录（每个资源取最近 N 条）
    window_start = datetime.now(timezone.utc) - timedelta(days=CHANGE_CONTEXT_WINDOW_DAYS)
    log_result = await session.execute(
        select(CmdbChangeLog)
        .where(
            CmdbChangeLog.resource_id.in_(resource_ids),
            CmdbChangeLog.created_at >= window_start,
        )
        .order_by(CmdbChangeLog.created_at.desc())
    )
    logs_by_resource: dict[int, list[CmdbChangeLog]] = {}
    for log in log_result.scalars().all():
        bucket = logs_by_resource.setdefault(log.resource_id, [])
        if len(bucket) < CHANGE_CONTEXT_MAX_CHANGES:
            bucket.append(log)

    # 4. 占用中的任务执行（同并发目标锁口径）
    busy_map: dict[int, int] = {}
    for exe in await JobExecutionRepo(session).list_active():
        for target in exe.target_resources or []:
            rid = target.get("resource_id")
            if rid is not None:
                busy_map.setdefault(rid, exe.id)

    # 4.5 影响该资源的活跃工单（判断变更时点用）
    active_ticket_rows = await TicketRepo(session).list_by_statuses(
        ("pending_approval", "open", "in_progress"),
    )
    tickets_by_resource: dict[int, list[dict]] = {}
    for t in active_ticket_rows:
        for rid in _extract_target_ids_from_ticket(t):
            tickets_by_resource.setdefault(rid, []).append(
                {"id": t.id, "ticket_no": t.ticket_no, "status": t.status, "title": t.title},
            )

    # 5. 当前生效的封禁窗口（全局命中所有资源，scope 按模型命中）
    active_freezes = await ChangeFreezeRepo(session).list_freezes(active_only=True)

    items: list[ChangeContextResource] = []
    for rid in resource_ids:
        resource, model_code = rows[rid]
        is_k8s = model_code.startswith("k8s_")
        hits = [
            f for f in active_freezes
            if not f.scope or model_code in f.scope
        ]
        items.append(ChangeContextResource(
            resource_id=rid,
            name=resource.name,
            model_code=model_code,
            status=resource.status,
            env=resolve_env(tags_by_resource.get(rid, []), is_k8s=is_k8s),
            recent_changes=[
                {
                    "id": log.id,
                    "change_type": log.change_type,
                    "field": log.field,
                    "source": log.source,
                    "operator": log.operator,
                    "created_at": log.created_at.isoformat(),
                }
                for log in logs_by_resource.get(rid, [])
            ],
            busy_execution_id=busy_map.get(rid),
            active_tickets=tickets_by_resource.get(rid, []),
            active_freezes=[
                {
                    "id": f.id,
                    "name": f.name,
                    "reason": f.reason,
                    "starts_at": f.starts_at.isoformat(),
                    "ends_at": f.ends_at.isoformat(),
                }
                for f in hits
            ],
        ))
    return items
