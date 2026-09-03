"""MCP 写工具白名单（SKILL §7：单列文件 + BINGOPS_MCP_WRITE_ENABLED 总开关，默认关闭）。"""

from __future__ import annotations

from bingops.core.config import settings
from bingops.core.exceptions import BingOpsError
from bingops.mcp._shared import get_agent_user, mcp_tool_logging, session_scope
from bingops.mcp.server import mcp
from bingops.services import ticket_service


def _ensure_write_enabled() -> None:
    if not settings.mcp_write_enabled:
        raise BingOpsError(
            "MCP write tools are disabled: set BINGOPS_MCP_WRITE_ENABLED=true to enable",
            code=40301, http_status=403,
        )


@mcp.tool()
@mcp_tool_logging("add_ticket_comment")
async def add_ticket_comment(ticket_id: int, content: str) -> dict:
    """在工单上追加评论，用于将 AI 产出（预检结论/日报摘要）落档到工单。

    适用场景：上线变更风险预检结果写回工单评论、巡检日报归档到运维工单。
    限制：受 BINGOPS_MCP_WRITE_ENABLED 总开关控制（默认关闭）；终态工单禁止评论；
    操作者为 BINGOPS_MCP_AGENT_USER_ID 指定的系统账号。
    """
    _ensure_write_enabled()
    async with session_scope() as session:
        operator = await get_agent_user(session)
        comment = await ticket_service.add_comment(session, ticket_id, content, operator)
    return {
        "comment_id": comment.id,
        "ticket_id": ticket_id,
        "created_at": comment.created_at.isoformat(),
    }
