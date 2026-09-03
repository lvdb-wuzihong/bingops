"""MCP 工具：作业执行记录（C 组，设计文档 §4.3-C）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bingops.mcp._shared import clamp_limit, mcp_tool_logging, pick_fields, session_scope
from bingops.mcp.server import mcp
from bingops.services import job_service

_EXECUTION_FIELDS = (
    "id", "runbook_id", "runbook_version", "code_ref", "status", "ticket_id",
    "triggered_by", "target_resources", "started_at", "finished_at", "created_at",
)


@mcp.tool()
@mcp_tool_logging("list_job_executions")
async def list_job_executions(
    status: str | None = None,
    runbook_id: int | None = None,
    hours_back: int = 24,
    limit: int | None = None,
) -> dict:
    """查询作业执行记录（含 git tag 快照 code_ref 与目标资源清单），默认近 24 小时。

    适用场景：巡检日报"昨日变更"段、根因分析将告警时间与发布 tag 对齐、
    变更风险预检统计目标资源近期变更频率。
    限制：仅第 1 页（默认 20、上限 100）再按时间窗过滤，total 为过滤后条数；
    不含 params/steps_snapshot/connection 大字段。
    """
    size = clamp_limit(limit)
    async with session_scope() as session:
        rows, _total = await job_service.list_executions(
            session, status=status, runbook_id=runbook_id, page=1, page_size=size,
        )
    since = (
        datetime.now(UTC) - timedelta(hours=hours_back) if hours_back > 0 else None
    )
    items = sorted(
        (
            pick_fields(e.__dict__, list(_EXECUTION_FIELDS))
            for e in rows if since is None or e.created_at >= since
        ),
        key=lambda x: x["created_at"], reverse=True,
    )
    return {"items": items, "total": len(items)}
