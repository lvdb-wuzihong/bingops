"""MCP 工具：告警事件与规则（E 组）。

监控终态（夜莺/ck-log-alert 已退役）后，告警数据的权威源为平台自身
（alert_events 统一事件表 + 执行器回报），本组工具是该数据面的 agent 出口。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bingops.core.exceptions import ValidationError
from bingops.mcp._shared import clamp_limit, mcp_tool_logging, pick_fields, session_scope
from bingops.mcp.server import mcp
from bingops.repositories.alert_repo import AlertEventRepo
from bingops.schemas.alert import VALID_GROUP_BYS
from bingops.services import alert_service

_EVENT_LIST_FIELDS = (
    "id", "source", "rule_code", "rule_name", "status", "severity", "rule_kind",
    "first_seen_at", "last_seen_at", "resolved_at", "total_count",
    "resource_ids", "labels", "ticket_id", "group_id",
)
_EVENT_DETAIL_FIELDS = _EVENT_LIST_FIELDS + (
    "details", "error", "resolve_reason", "window_start", "window_end",
    "monitoring_source_id",
)
_MUTE_FIELDS = (
    "id", "name", "rule_code", "labels", "starts_at", "ends_at",
    "note", "enabled", "created_by",
)
_RULE_FIELDS = (
    "id", "source", "code", "name", "group_id", "stale_minutes",
    "default_severity", "static_labels", "notify_enabled", "enabled", "source_id",
)


def _cutoff(hours_back: int) -> datetime | None:
    """时间窗起点；hours_back<=0 表示不过滤。"""
    if hours_back <= 0:
        return None
    return datetime.now(UTC) - timedelta(hours=hours_back)


@mcp.tool()
@mcp_tool_logging("list_alert_events")
async def list_alert_events(
    status: str | None = None,
    source: str | None = None,
    rule_code: str | None = None,
    rule_kind: str | None = None,
    hours_back: int = 24,
    limit: int | None = None,
) -> dict:
    """查询告警事件列表（firing/resolved/error），默认近 24 小时，含关联资源 ID 与标签。

    适用场景：根因分析的事件入口（告警 → resource_ids 反查应用与变更）、
    巡检日报"未恢复告警"段（status=firing）、告警噪声分析。
    限制：仅第 1 页（默认 20、上限 100）再按时间窗过滤（按 first_seen_at），
    total 为过滤后条数；details 明细黑盒不在列表返回（用 get_alert_event 取）。
    """
    size = clamp_limit(limit)
    async with session_scope() as session:
        items, _total = await AlertEventRepo(session).list(
            status=status, source=source, rule_code=rule_code, rule_kind=rule_kind,
            since=_cutoff(hours_back), until=None, page=1, page_size=size,
        )
    events = sorted(
        (pick_fields(e.__dict__, list(_EVENT_LIST_FIELDS)) for e in items),
        key=lambda x: x["first_seen_at"], reverse=True,
    )
    return {"items": events, "total": len(events)}


@mcp.tool()
@mcp_tool_logging("get_alert_event")
async def get_alert_event(event_id: int) -> dict:
    """获取单条告警事件详情：状态机时间线、labels、关联资源、来源明细（details）与错误信息。

    适用场景：根因分析对重点告警取证（details 含命中样本；error 为评估失败原因）。
    限制：事件不存在返回 not_found；details 形态随来源不同（日志型为文本、指标型为样本列表）。
    """
    async with session_scope() as session:
        event = await AlertEventRepo(session).get_by_id(event_id)
    if event is None:
        from bingops.core.exceptions import NotFoundError

        raise NotFoundError("AlertEvent", str(event_id))
    return pick_fields(event.__dict__, list(_EVENT_DETAIL_FIELDS))


@mcp.tool()
@mcp_tool_logging("get_alert_stats")
async def get_alert_stats(
    group_by: str = "source",
    rule_kind: str | None = None,
    hours_back: int = 24,
) -> dict:
    """告警聚合统计：各分组状态计数、当前活跃 firing 总数、平均恢复时长。

    适用场景：巡检日报总览数字、告警高频规则排名（噪声治理清单）、周期趋势对比。
    限制：group_by 仅支持 source|rule_code|group_id|day|rule_kind；
    「活跃 firing」为当前全量实时值，不受时间窗影响。
    """
    if group_by not in VALID_GROUP_BYS:
        raise ValidationError(f"group_by must be one of: {VALID_GROUP_BYS}")
    async with session_scope() as session:
        return await alert_service.stats_summary(
            session, group_by=group_by, since=_cutoff(hours_back), until=None,
            rule_kind=rule_kind,
        )


@mcp.tool()
@mcp_tool_logging("list_alert_rules")
async def list_alert_rules() -> dict:
    """列出告警规则映射（rule_code → 数据源/处理组/防抖窗口/静态标签/通知开关）。

    适用场景：根因分析回答"哪条规则触发的、判定逻辑与防抖配置"；
    规则治理审查（未绑定数据源/通知关闭的规则盘点）。
    限制：返回全部规则（不分页）；评估契约原文（eval_sql/PromQL）请按需在平台管理面查看。
    """
    async with session_scope() as session:
        rules = await alert_service.list_rules(session)
    items = [pick_fields(r.__dict__, list(_RULE_FIELDS)) for r in rules]
    return {"items": items, "total": len(items)}


@mcp.tool()
@mcp_tool_logging("list_alert_mutes")
async def list_alert_mutes(active_only: bool = False) -> dict:
    """列出告警屏蔽规则（rule_code/labels 匹配器 + 时间窗），默认含全部历史。

    适用场景：根因分析判断告警是否处于屏蔽期（噪音排除）、巡检报告标注静默中的告警。
    限制：active_only=true 时仅返回"启用且在时间窗内"的记录；屏蔽仅抑制通知，事件照常落库。
    """
    async with session_scope() as session:
        mutes = await alert_service.list_mutes(session)
    now = datetime.now(UTC)
    items = [
        pick_fields(m.__dict__, list(_MUTE_FIELDS))
        for m in mutes
        if not active_only or (m.enabled and m.starts_at <= now <= m.ends_at)
    ]
    return {"items": items, "total": len(items)}
