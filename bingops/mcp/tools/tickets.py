"""MCP 工具：工单与变更上下文（C 组，设计文档 §4.3-C）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bingops.core.exceptions import ValidationError
from bingops.mcp._shared import clamp_limit, mcp_tool_logging, pick_fields, session_scope
from bingops.mcp.server import mcp
from bingops.services import change_freeze_service, ticket_service

_TICKET_FIELDS = (
    "id", "ticket_no", "title", "status", "ticket_type", "priority", "risk_level",
    "approval_status", "creator_id", "assignee_id", "catalog_item_id", "group_id",
    "business_app_id", "target_resource_ids", "code_ref", "runbook_id",
    "created_at", "updated_at", "resolved_at",
)

_MAX_CONTEXT_RESOURCES = 50


def _cutoff(hours_back: int) -> datetime | None:
    """时间窗起点；hours_back<=0 表示不过滤。"""
    if hours_back <= 0:
        return None
    return datetime.now(UTC) - timedelta(hours=hours_back)


@mcp.tool()
@mcp_tool_logging("list_tickets")
async def list_tickets(
    status: str | None = None,
    ticket_type: str | None = None,
    priority: str | None = None,
    keyword: str | None = None,
    hours_back: int = 24,
    limit: int | None = None,
) -> dict:
    """按条件查询工单（状态/类型/优先级/关键词 + 时间窗），返回裁剪后的工单字段。

    适用场景：巡检日报"昨日变更回顾"（ticket_type=change）、复盘时检索 incident 工单。
    限制：仅第 1 页（默认 20、上限 100）再按时间窗过滤，total 为过滤后条数；
    P0 未暴露 creator/assignee 等人维度过滤参数。
    """
    size = clamp_limit(limit)
    async with session_scope() as session:
        rows, _total = await ticket_service.list_tickets(
            session, status=status, ticket_type=ticket_type, priority=priority,
            keyword=keyword, page=1, page_size=size,
        )
    since = _cutoff(hours_back)
    items = sorted(
        (
            pick_fields(t.__dict__, list(_TICKET_FIELDS))
            for t in rows if since is None or t.created_at >= since
        ),
        key=lambda x: x["created_at"], reverse=True,
    )
    return {"items": items, "total": len(items)}


@mcp.tool()
@mcp_tool_logging("get_ticket_timeline")
async def get_ticket_timeline(ticket_id: int) -> dict:
    """获取工单时间线：工单概要 + 全部流转/评论记录（按时间升序）。

    适用场景：故障复盘初稿的时间线素材（处置动作序列）、变更风险预检了解目标工单处理历史。
    限制：不含审批记录与执行步骤明细；工单不存在返回 not_found。
    """
    async with session_scope() as session:
        ticket = await ticket_service.get_ticket(session, ticket_id)
        comments = await ticket_service.list_comments(session, ticket_id)

    timeline = sorted(
        (
            {
                "id": c.id, "user_id": c.user_id, "action": c.action,
                "content": c.content, "from_value": c.from_value, "to_value": c.to_value,
                "created_at": c.created_at.isoformat(),
            }
            for c in comments
        ),
        key=lambda x: x["created_at"],
    )
    return {"ticket": pick_fields(ticket.__dict__, list(_TICKET_FIELDS)), "timeline": timeline}


@mcp.tool()
@mcp_tool_logging("get_change_context")
async def get_change_context(resource_ids: list[int]) -> dict:
    """聚合目标资源的变更上下文：近 7 天变更记录、活跃工单、占用中执行、封禁窗口、环境标签。

    适用场景：上线变更风险预检的核心数据源（变更时点是否合适）、
    根因分析判断告警资源近期是否发生过变更。
    限制：最多 50 个资源；resource_id 不存在时返回 not_found（先用 search_resources 确认）。
    """
    if not resource_ids:
        raise ValidationError("resource_ids is required")
    if len(resource_ids) > _MAX_CONTEXT_RESOURCES:
        raise ValidationError(f"resource_ids exceeds max {_MAX_CONTEXT_RESOURCES} per call")

    async with session_scope() as session:
        items = await ticket_service.change_context(session, list(resource_ids))
    return {"resources": [i.model_dump(mode="json") for i in items]}


@mcp.tool()
@mcp_tool_logging("list_freezes")
async def list_freezes(active_only: bool = True) -> dict:
    """查询变更封禁窗口（默认仅当前生效），含生效时间与适用模型范围。

    适用场景：变更风险预检判断变更时点是否落在封禁期、根因分析排除封禁期内的变更。
    限制：scope 为模型 code 列表（空列表 = 全局生效）；不支持历史窗口全文检索。
    """
    async with session_scope() as session:
        freezes = await change_freeze_service.list_freezes(session, active_only=active_only)
    items = [
        {
            "id": f.id, "name": f.name, "reason": f.reason, "scope": f.scope,
            "starts_at": f.starts_at.isoformat(), "ends_at": f.ends_at.isoformat(),
            "created_by": f.created_by,
        }
        for f in freezes
    ]
    return {"items": items, "total": len(items)}
