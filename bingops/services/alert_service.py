"""告警事件闭环服务层（设计见 docs/monitoring-design.md §6）。

状态机只有 DB 一个事实源：firing --(resolved 直传 | stale 超时)--> resolved；
error 为独立旁路行。全部写操作幂等（uq_alert_active_firing 兜底），
多副本部署下重复处理无害，stale 扫描循环无内存状态。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bingops.core.config import settings
from bingops.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from bingops.models.alert import (
    AlertEvent,
    AlertRule,
    MonitoringSource,
    NotifyChannel,
)
from bingops.models.cmdb.resource import CmdbResource
from bingops.models.ticket import Ticket
from bingops.models.user import User
from bingops.repositories.alert_repo import (
    AlertEventRepo,
    AlertRuleRepo,
    MonitoringSourceRepo,
    NotifyChannelRepo,
)
from bingops.schemas.alert import (
    VALID_SEVERITIES,
    AgentConfigResponse,
    AgentNotifyChannel,
    AgentRuleConfig,
    AgentSourceConfig,
    AlertRuleCreate,
    AlertRuleUpdate,
    AlertWebhookPayload,
    MonitoringSourceCreate,
    MonitoringSourceUpdate,
    NotifyChannelCreate,
    NotifyChannelUpdate,
)

logger = logging.getLogger(f"bingops.{__name__}")

# 活跃 firing 重复提醒间隔（夜莺 repeat notify 同款；协议预留，执行器可选读）
REPEAT_NOTIFY_MINUTES = 30
# 无规则映射的 firing 的默认 stale 恢复窗口（有映射时取规则 stale_minutes）
DEFAULT_STALE_MINUTES = 15
# stale 扫描周期
STALE_SWEEP_INTERVAL_SECONDS = 60
# severity → 工单优先级映射（urgent 留给人为判断）
SEVERITY_TO_PRIORITY = {1: "high", 2: "medium", 3: "low"}
# CMDB 尽力匹配的 labels 键（hostname → 主机资源；pod* → k8s pod 资源）
MATCH_LABEL_KEYS = ("hostname", "pod_name", "pod")
# 开单描述中 details 摘要的最大长度
_TICKET_DETAILS_MAX_CHARS = 2000


# ── Webhook 事件处理（状态机入口） ────────────────────────────────────────────


async def handle_webhook_event(
    session: AsyncSession, payload: AlertWebhookPayload,
) -> dict:
    """处理执行器/夜莺回报的告警事件，返回 notify 协议响应体。

    Raises:
        ValidationError: status=error 且 error 为空等契约问题。
    """
    now = datetime.now(UTC)
    repo = AlertEventRepo(session)
    rule = await AlertRuleRepo(session).get_by_source_code(
        payload.source, payload.rule_code,
    )
    # 事件的数据源归属：取规则绑定的数据源（幂等归组键的一部分，防跨数据源同名规则吞没）
    ds_id = rule.source_id if rule else None

    # error 旁路：独立落行不进状态机；但需顺延活跃 firing 的推导窗口——
    # 评估失败说明「状态未知」，保守保持告警而非让 stale 超时制造假恢复
    if payload.status == "error":
        active = await repo.get_active_firing(payload.source, payload.rule_code, ds_id)
        if active is not None:
            active.last_seen_at = now
            await repo.update(active)
        event = await repo.create(AlertEvent(
            source=payload.source,
            rule_code=payload.rule_code,
            rule_name=payload.rule_name,
            status="error",
            window_start=payload.window_start,
            window_end=payload.window_end,
            first_seen_at=now,
            last_seen_at=now,
            labels=_merged_labels(rule, payload.labels),
            details=payload.details,
            error=payload.error,
            monitoring_source_id=ds_id,
            group_id=rule.group_id if rule else None,
        ))
        await session.commit()
        logger.warning(
            "Alert evaluation error recorded",
            extra={
                "source": payload.source,
                "rule_code": payload.rule_code,
                "error_message": (payload.error or "")[:200],
            },
        )
        return _webhook_result(event.id, notify=False, suppress_reason="error event recorded")

    # resolved 直传（仅夜莺等自带恢复语义的来源）
    if payload.status == "resolved":
        active = await repo.get_active_firing(payload.source, payload.rule_code, ds_id)
        if active is None:
            # 无活跃 firing 的 resolved 属于迟到的重复通知，忽略即可
            logger.debug(
                "Resolved event without active firing ignored",
                extra={"source": payload.source, "rule_code": payload.rule_code},
            )
            return _webhook_result(None, notify=False, suppress_reason="no active firing")
        active.status = "resolved"
        active.resolved_at = now
        active.resolve_reason = "resolved_event"
        await repo.update(active)
        await session.commit()
        await _after_resolved(session, active)
        return _webhook_result(active.id, notify=False, suppress_reason="resolved")

    # firing：合并或新建
    active = await repo.get_active_firing(payload.source, payload.rule_code, ds_id)
    if active is not None:
        # repeat 判断必须在更新 updated_at 之前（updated_at ≈ 上次通知基准）
        repeat_due = (
            now - active.updated_at
        ).total_seconds() >= REPEAT_NOTIFY_MINUTES * 60
        active.last_seen_at = now
        if payload.total_count is not None:
            active.total_count = payload.total_count
        if payload.details is not None:
            active.details = payload.details
        active.labels = _merged_labels(rule, payload.labels)
        await repo.update(active)
        await session.commit()
        if repeat_due:
            logger.info(
                "Alert firing repeat notification due",
                extra={"source": payload.source, "rule_code": payload.rule_code},
            )
            return _webhook_result(active.id, notify=True)
        return _webhook_result(
            active.id, notify=False, suppress_reason="firing ongoing",
        )

    # 新建 firing
    severity = payload.severity or (rule.default_severity if rule else 2)
    event = await repo.create(AlertEvent(
        source=payload.source,
        rule_code=payload.rule_code,
        rule_name=payload.rule_name,
        status="firing",
        window_start=payload.window_start,
        window_end=payload.window_end,
        first_seen_at=now,
        last_seen_at=now,
        total_count=payload.total_count or 0,
        severity=severity,
        labels=_merged_labels(rule, payload.labels),
        resource_ids=await _match_resources(session, payload.labels),
        details=payload.details,
        monitoring_source_id=ds_id,
        group_id=rule.group_id if rule else None,
    ))
    try:
        await session.commit()
    except IntegrityError:
        # 并发兜底：活跃 firing 已被其他请求创建 → 退化为合并路径
        await session.rollback()
        raced = await repo.get_active_firing(payload.source, payload.rule_code, ds_id)
        if raced is not None:
            raced.last_seen_at = now
            await repo.update(raced)
            await session.commit()
            return _webhook_result(
                raced.id, notify=False, suppress_reason="concurrent firing merged",
            )
        raise

    await _try_open_ticket(session, event, rule)
    return _webhook_result(event.id, notify=True)


def _webhook_result(
    event_id: int | None, *, notify: bool, suppress_reason: str | None = None,
) -> dict:
    """组装 notify 协议响应体。"""
    return {
        "event_id": event_id,
        "notify": notify,
        "suppress_reason": suppress_reason,
        "repeat_after_minutes": REPEAT_NOTIFY_MINUTES,
    }


def _merged_labels(rule: AlertRule | None, payload_labels: dict) -> dict:
    """执行器 labels 优先，平台 static_labels 补缺。"""
    merged = dict(rule.static_labels) if rule else {}
    merged.update(payload_labels or {})
    return merged


# ── CMDB 资源关联（尽力匹配，不阻塞） ────────────────────────────────────────


async def _match_resources(session: AsyncSession, labels: dict | None) -> list[int]:
    """labels 中的 hostname/pod 与 CMDB 资源 name 尽力匹配；全不中返回空。"""
    values = [
        labels[key] for key in MATCH_LABEL_KEYS
        if isinstance(labels, dict) and isinstance(labels.get(key), str) and labels[key]
    ]
    if not values:
        return []
    result = await session.execute(
        select(CmdbResource.id).where(
            CmdbResource.name.in_(values),
            CmdbResource.deleted_at.is_(None),
        )
    )
    return [row[0] for row in result.all()]


# ── 工单联动（开单 + 恢复流转） ──────────────────────────────────────────────


async def _get_alert_operator(session: AsyncSession) -> User | None:
    """告警自动开单的系统操作者（BINGOPS_ALERT_OPERATOR_ID；0 = 未配置）。"""
    if settings.alert_operator_id <= 0:
        return None
    return await session.get(User, settings.alert_operator_id)


async def _try_open_ticket(
    session: AsyncSession, event: AlertEvent, rule: AlertRule | None,
) -> None:
    """首次 firing 自动开单：按规则映射处理组走现有值班自动派单链路。

    全局总闸 alert_ticket_enabled 关闭时直接短路（告警不碰工单，用户决策）。
    未知 rule_code / notify_enabled=false / 操作者未配置 → 只记事件不开单
    （「有数无单」可观测可补配，而不是失败）。
    """
    if not settings.alert_ticket_enabled:
        return
    if rule is None:
        logger.warning(
            "Alert rule mapping not found, ticket skipped",
            extra={"source": event.source, "rule_code": event.rule_code},
        )
        return
    if not rule.notify_enabled:
        return

    from bingops.schemas.ticket import TicketCreate
    from bingops.services import ticket_service

    operator = await _get_alert_operator(session)
    if operator is None:
        logger.warning(
            "Alert operator not configured, ticket skipped",
            extra={"source": event.source, "rule_code": event.rule_code},
        )
        return

    payload = TicketCreate(
        title=f"[告警] {event.rule_name or event.rule_code}",
        description=_ticket_description(event),
        ticket_type="incident",
        priority=SEVERITY_TO_PRIORITY.get(event.severity, "medium"),
        group_id=rule.group_id,
    )
    try:
        ticket = await ticket_service.create_ticket(session, payload, operator)
    except (ValidationError, NotFoundError) as exc:
        logger.warning(
            "Alert ticket creation skipped",
            extra={
                "event_id": event.id,
                "reason": str(exc.message),
            },
        )
        return

    event.ticket_id = ticket.id
    await AlertEventRepo(session).update(event)
    await session.commit()
    logger.info(
        "Alert ticket opened",
        extra={
            "event_id": event.id,
            "ticket_id": ticket.id,
            "group_id": rule.group_id,
        },
    )


def _ticket_description(event: AlertEvent) -> str:
    """开单描述：时间窗 + 计数 + 来源 + details 摘要（截断）。"""
    window = ""
    if event.window_start and event.window_end:
        window = f"{event.window_start.isoformat()} ~ {event.window_end.isoformat()}\n"
    details_text = json.dumps(event.details, ensure_ascii=False, default=str) \
        if event.details is not None else ""
    if len(details_text) > _TICKET_DETAILS_MAX_CHARS:
        details_text = details_text[:_TICKET_DETAILS_MAX_CHARS] + "\n... (truncated)"
    return (
        f"错误总数（含重复）：{event.total_count}\n"
        f"时间窗口：{window}"
        f"来源：{event.source} / 规则：{event.rule_code}\n"
        f"严重级别：{event.severity}\n"
        f"\n{details_text}"
    )


async def _after_resolved(session: AsyncSession, event: AlertEvent) -> None:
    """告警恢复后联动工单流转；未开单/操作者缺失/流转非法时降级为日志。

    全局总闸 alert_ticket_enabled 关闭时不碰工单（历史遗留关联也保持原状）。
    """
    if not settings.alert_ticket_enabled:
        return
    if event.ticket_id is None:
        return
    operator = await _get_alert_operator(session)
    if operator is None:
        logger.warning(
            "Alert operator not configured, ticket transition skipped",
            extra={"event_id": event.id, "ticket_id": event.ticket_id},
        )
        return

    from bingops.services import ticket_service

    ticket = await session.get(Ticket, event.ticket_id)
    if ticket is None or ticket.status not in ("open", "in_progress"):
        return

    comment = "[auto] alert resolved"
    try:
        if ticket.status == "open":
            # 状态矩阵 open 不能直达 resolved：自动接管后再完成
            await ticket_service.change_ticket_status(
                session, event.ticket_id, "in_progress", operator, comment,
            )
        await ticket_service.change_ticket_status(
            session, event.ticket_id, "resolved", operator, comment,
        )
    except (ValidationError, PermissionDeniedError) as exc:
        logger.warning(
            "Alert ticket transition failed",
            extra={
                "event_id": event.id,
                "ticket_id": event.ticket_id,
                "reason": exc.message,
            },
        )
        return
    logger.info(
        "Alert ticket resolved",
        extra={"event_id": event.id, "ticket_id": event.ticket_id},
    )


# ── stale 扫描后台任务 ───────────────────────────────────────────────────────


async def alert_stale_sweep_loop(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """60s 周期扫描活跃 firing 超过 stale_minutes 的置 resolved。

    纯 DB 驱动的幂等 UPDATE，无内存状态，多副本部署无害（设计决策 1）。
    """
    logger.info("Alert stale sweep loop started")
    while True:
        try:
            async with session_factory() as session:
                await _sweep_stale_events(session)
        except Exception:
            logger.exception("Alert stale sweep failed")
        await asyncio.sleep(STALE_SWEEP_INTERVAL_SECONDS)


async def _sweep_stale_events(session: AsyncSession) -> None:
    """将超过 stale 窗口的活跃 firing 置 resolved 并联动工单。"""
    repo = AlertEventRepo(session)
    firings = await repo.list_active_firings()
    if not firings:
        return

    now = datetime.now(UTC)
    rules = await AlertRuleRepo(session).list_all()
    stale_by_key = {
        (rule.source, rule.code): rule.stale_minutes for rule in rules
    }

    expired: list[AlertEvent] = []
    for event in firings:
        minutes = stale_by_key.get(
            (event.source, event.rule_code), DEFAULT_STALE_MINUTES,
        )
        if event.last_seen_at is None:
            continue
        if (now - event.last_seen_at).total_seconds() >= minutes * 60:
            event.status = "resolved"
            event.resolved_at = now
            event.resolve_reason = "stale_timeout"
            expired.append(event)

    if not expired:
        return
    for event in expired:
        await repo.update(event)
    await session.commit()
    for event in expired:
        await _after_resolved(session, event)
        logger.info(
            "Alert firing resolved by stale timeout",
            extra={
                "event_id": event.id,
                "source": event.source,
                "rule_code": event.rule_code,
            },
        )


# ── 规则映射 CRUD（service 编排） ────────────────────────────────────────────


async def list_rules(session: AsyncSession) -> list[AlertRule]:
    return await AlertRuleRepo(session).list_all()


async def create_rule(session: AsyncSession, payload: AlertRuleCreate) -> AlertRule:
    _validate_rule_payload(payload.default_severity)
    await _validate_group_exists(session, payload.group_id)
    await _validate_source_exists(session, payload.source_id)
    rule = await AlertRuleRepo(session).create(AlertRule(
        source=payload.source,
        code=payload.code,
        name=payload.name,
        group_id=payload.group_id,
        stale_minutes=payload.stale_minutes,
        default_severity=payload.default_severity,
        static_labels=payload.static_labels,
        notify_enabled=payload.notify_enabled,
        enabled=payload.enabled,
        source_id=payload.source_id,
        eval_sql=payload.eval_sql,
        threshold=payload.threshold,
        interval_minutes=payload.interval_minutes,
        eval_interval_seconds=payload.eval_interval_seconds,
        for_rounds=payload.for_rounds,
        detail_limit=payload.detail_limit,
        grafana_url=payload.grafana_url,
        feishu_card_template=payload.feishu_card_template,
        notify_channel_id=payload.notify_channel_id,
    ))
    await session.commit()
    logger.info(
        "Alert rule mapping created",
        extra={"rule_id": rule.id, "source": rule.source, "rule_code": rule.code},
    )
    return rule


async def update_rule(
    session: AsyncSession, rule_id: int, payload: AlertRuleUpdate,
) -> AlertRule:
    rule = await AlertRuleRepo(session).get_by_id(rule_id)
    if rule is None:
        raise NotFoundError("AlertRule", str(rule_id))
    if payload.default_severity is not None:
        _validate_rule_payload(payload.default_severity)
    if payload.group_id is not None:
        await _validate_group_exists(session, payload.group_id)
    if payload.source_id is not None:
        await _validate_source_exists(session, payload.source_id)
    if payload.notify_channel_id is not None:
        await _validate_channel_exists(session, payload.notify_channel_id)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)

    rule = await AlertRuleRepo(session).update(rule)
    await session.commit()
    logger.info("Alert rule mapping updated", extra={"rule_id": rule_id})
    return rule


async def delete_rule(session: AsyncSession, rule_id: int) -> None:
    rule = await AlertRuleRepo(session).get_by_id(rule_id)
    if rule is None:
        raise NotFoundError("AlertRule", str(rule_id))
    await AlertRuleRepo(session).delete(rule)
    await session.commit()
    logger.info("Alert rule mapping deleted", extra={"rule_id": rule_id})


def _validate_rule_payload(severity: int) -> None:
    if severity not in VALID_SEVERITIES:
        raise ValidationError(f"severity must be one of: {VALID_SEVERITIES}")


async def _validate_group_exists(session: AsyncSession, group_id: int | None) -> None:
    if group_id is None:
        return
    from bingops.models.ticket import TicketGroup

    group = await session.get(TicketGroup, group_id)
    if group is None:
        raise NotFoundError("TicketGroup", str(group_id))


async def _validate_source_exists(
    session: AsyncSession, source_id: int | None,
) -> None:
    if source_id is None:
        return
    if await MonitoringSourceRepo(session).get_by_id(source_id) is None:
        raise NotFoundError("MonitoringSource", str(source_id))


async def _validate_channel_exists(
    session: AsyncSession, channel_id: int | None,
) -> None:
    if channel_id is None:
        return
    if await NotifyChannelRepo(session).get_by_id(channel_id) is None:
        raise NotFoundError("NotifyChannel", str(channel_id))


async def _validate_channel_exists(
    session: AsyncSession, channel_id: int | None,
) -> None:
    if channel_id is None:
        return
    if await NotifyChannelRepo(session).get_by_id(channel_id) is None:
        raise NotFoundError("NotifyChannel", str(channel_id))


# ── 统计（§9） ───────────────────────────────────────────────────────────────


async def stats_summary(
    session: AsyncSession,
    *,
    group_by: str,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict:
    """各分组状态计数 + 当前活跃 firing 总数 + 平均恢复时长。"""
    repo = AlertEventRepo(session)
    groups = await repo.stats_grouped(group_by=group_by, since=since, until=until)
    avg_seconds = await repo.avg_resolve_seconds(since=since, until=until)
    active_total = sum(item["firing_count"] for item in groups)
    return {
        "group_by": group_by,
        "groups": groups,
        "active_firing_total": active_total,
        "avg_resolve_seconds": round(avg_seconds, 1) if avg_seconds is not None else None,
    }


# ── 监控数据源 CRUD（二期：§12 数据源注册表） ─────────────────────────────────


async def list_sources(session: AsyncSession) -> list[MonitoringSource]:
    return await MonitoringSourceRepo(session).list_all()


async def create_source(
    session: AsyncSession, payload: MonitoringSourceCreate,
) -> MonitoringSource:
    src = await MonitoringSourceRepo(session).create(MonitoringSource(
        name=payload.name,
        type=payload.type,
        host=payload.host,
        port=payload.port,
        database_name=payload.database_name,
        username=payload.username,
        password_ref=payload.password_ref,
        secure=payload.secure,
        enabled=payload.enabled,
    ))
    await session.commit()
    logger.info(
        "Monitoring source created",
        extra={"source_id": src.id, "source_name": src.name, "source_type": src.type},
    )
    return src


async def update_source(
    session: AsyncSession, source_id: int, payload: MonitoringSourceUpdate,
) -> MonitoringSource:
    src = await MonitoringSourceRepo(session).get_by_id(source_id)
    if src is None:
        raise NotFoundError("MonitoringSource", str(source_id))

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(src, field, value)

    src = await MonitoringSourceRepo(session).update(src)
    await session.commit()
    logger.info("Monitoring source updated", extra={"source_id": source_id})
    return src


async def delete_source(session: AsyncSession, source_id: int) -> None:
    """删除数据源；仍有规则绑定时阻断（避免分发体出现孤儿规则）。"""
    src = await MonitoringSourceRepo(session).get_by_id(source_id)
    if src is None:
        raise NotFoundError("MonitoringSource", str(source_id))

    bound = await AlertRuleRepo(session).list_agent_rules()
    if any(rule.source_id == source_id for rule, _src in bound):
        raise ConflictError(
            f"Monitoring source {source_id} is bound by enabled alert rules"
        )

    await MonitoringSourceRepo(session).delete(src)
    await session.commit()
    logger.info("Monitoring source deleted", extra={"source_id": source_id})


# ── Agent 分发（二期：§12 分发 API） ─────────────────────────────────────────


async def build_agent_config(session: AsyncSession) -> dict:
    """组装执行器拉取的分发体：启用规则 + 启用数据源（凭据只带引用名）。

    未绑定数据源或数据源被禁用的规则不分发（无法评估）。
    """
    pairs = await AlertRuleRepo(session).list_agent_rules()
    rules: list[AgentRuleConfig] = []
    for rule, src, channel in pairs:
        rules.append(AgentRuleConfig(
            id=rule.id,
            source=rule.source,
            code=rule.code,
            name=rule.name,
            interval_minutes=rule.interval_minutes,
            threshold=rule.threshold,
            for_rounds=rule.for_rounds,
            detail_limit=rule.detail_limit,
            eval_interval_seconds=rule.eval_interval_seconds,
            eval_sql=rule.eval_sql,
            stale_minutes=rule.stale_minutes,
            default_severity=rule.default_severity,
            group_id=rule.group_id,
            static_labels=rule.static_labels or {},
            grafana_url=rule.grafana_url,
            feishu_card_template=rule.feishu_card_template,
            notify_enabled=rule.notify_enabled,
            datasource=AgentSourceConfig(
                name=src.name,
                type=src.type,
                host=src.host,
                port=src.port,
                database_name=src.database_name,
                username=src.username,
                password_ref=src.password_ref,
                secure=src.secure,
            ),
            # 渠道被禁用时置 None：通知由执行器默认处理（评估与回报照常）
            notify_channel=(
                AgentNotifyChannel(
                    name=channel.name,
                    type=channel.type,
                    secret_ref=channel.secret_ref,
                    extra=channel.extra or {},
                )
                if channel is not None and channel.enabled else None
            ),
        ))
    logger.info("Agent config dispatched", extra={"rule_count": len(rules)})
    return AgentConfigResponse(rules=rules).model_dump(mode="json")


# ── 通知渠道 CRUD（二期：§12 渠道登记，发送仍在执行器） ──────────────────────


async def list_channels(session: AsyncSession) -> list[NotifyChannel]:
    return await NotifyChannelRepo(session).list_all()


async def create_channel(
    session: AsyncSession, payload: NotifyChannelCreate,
) -> NotifyChannel:
    channel = await NotifyChannelRepo(session).create(NotifyChannel(
        name=payload.name,
        type=payload.type,
        secret_ref=payload.secret_ref,
        extra=payload.extra,
        enabled=payload.enabled,
    ))
    await session.commit()
    logger.info(
        "Notify channel created",
        extra={"channel_id": channel.id, "channel_name": channel.name},
    )
    return channel


async def update_channel(
    session: AsyncSession, channel_id: int, payload: NotifyChannelUpdate,
) -> NotifyChannel:
    channel = await NotifyChannelRepo(session).get_by_id(channel_id)
    if channel is None:
        raise NotFoundError("NotifyChannel", str(channel_id))

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(channel, field, value)

    channel = await NotifyChannelRepo(session).update(channel)
    await session.commit()
    logger.info("Notify channel updated", extra={"channel_id": channel_id})
    return channel


async def delete_channel(session: AsyncSession, channel_id: int) -> None:
    """删除通知渠道；仍有规则绑定时阻断（避免规则通知悬空）。"""
    channel = await NotifyChannelRepo(session).get_by_id(channel_id)
    if channel is None:
        raise NotFoundError("NotifyChannel", str(channel_id))

    bound = await session.execute(
        select(AlertRule.id).where(AlertRule.notify_channel_id == channel_id)
    )
    if bound.first() is not None:
        raise ConflictError(
            f"Notify channel {channel_id} is bound by alert rules"
        )

    await NotifyChannelRepo(session).delete(channel)
    await session.commit()
    logger.info("Notify channel deleted", extra={"channel_id": channel_id})
