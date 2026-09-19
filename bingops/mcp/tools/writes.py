"""MCP 写工具白名单（SKILL §7：单列文件 + BINGOPS_MCP_WRITE_ENABLED 总开关，默认关闭）。"""

from __future__ import annotations

from bingops.core import feishu_bot
from bingops.core.config import settings
from bingops.core.exceptions import BingOpsError
from bingops.mcp._shared import brief, get_agent_user, mcp_tool_logging, session_scope
from bingops.mcp.server import mcp
from bingops.services import ticket_service


def _ensure_write_enabled() -> None:
    if not settings.mcp_write_enabled:
        raise BingOpsError(
            "MCP write tools are disabled: set BINGOPS_MCP_WRITE_ENABLED=true to enable",
            code=40301, http_status=403,
        )


def _ensure_feishu_chat_allowed(chat_id: str) -> None:
    """群目标白名单：BINGOPS_MCP_FEISHU_ALLOWED_CHATS 非空时仅允许列表内 chat_id。"""
    allowed = [c.strip() for c in settings.mcp_feishu_allowed_chats.split(",") if c.strip()]
    if allowed and chat_id not in allowed:
        raise BingOpsError(
            f"chat_id {brief(chat_id, 24)} not in BINGOPS_MCP_FEISHU_ALLOWED_CHATS allowlist",
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


@mcp.tool()
@mcp_tool_logging("send_feishu_message")
async def send_feishu_message(target: str, content: str, target_type: str = "chat") -> dict:
    """发送飞书文本消息（日报推送与 agent 回复的统一出站通道）。

    适用场景：巡检日报推送到群、对话结果送达用户；编排层不直连飞书，出站一律经平台 feishu_bot。
    限制：受 BINGOPS_MCP_WRITE_ENABLED 总开关控制；target_type=chat 受
    BINGOPS_MCP_FEISHU_ALLOWED_CHATS 白名单收窄（未配置则允许任意群）；仅文本消息。
    """
    _ensure_write_enabled()
    if target_type not in ("chat", "user"):
        raise BingOpsError(
            f"unsupported target_type: {target_type} (use chat | user)",
            code=40001, http_status=422,
        )
    receive_id_type = "chat_id" if target_type == "chat" else "open_id"
    if target_type == "chat":
        _ensure_feishu_chat_allowed(target)

    await feishu_bot.send_text(receive_id_type, target, content)
    return {"sent": True, "target_type": target_type, "target": brief(target, 32)}
