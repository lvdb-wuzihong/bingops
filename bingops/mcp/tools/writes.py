"""MCP 写工具白名单（SKILL §7：单列文件 + BINGOPS_MCP_WRITE_ENABLED 总开关，默认关闭）。"""

from __future__ import annotations

import json

from bingops.core import feishu_bot
from bingops.core.config import settings
from bingops.core.exceptions import BingOpsError, ValidationError
from bingops.mcp._shared import brief, get_agent_user, mcp_tool_logging, session_scope
from bingops.mcp.server import mcp
from bingops.services import ticket_service

# 飞书卡片 JSON 体积约束（超出需精简内容后重试）
CARD_MAX_BYTES = 30_000


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


def _ensure_card_size(card: dict) -> None:
    """卡片体积校验：飞书卡片 JSON 超限时拒绝，提示精简内容。"""
    size = len(json.dumps(card, ensure_ascii=False).encode("utf-8"))
    if size > CARD_MAX_BYTES:
        raise ValidationError(
            f"card too large: {size} bytes (max {CARD_MAX_BYTES}); trim the content and retry"
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


@mcp.tool()
@mcp_tool_logging("send_feishu_card")
async def send_feishu_card(target: str, card: dict, target_type: str = "chat") -> dict:
    """发送飞书交互卡片（结构化回复/报告展示），返回 message_id 供后续流式更新。

    适用场景：agent 回复卡片化（标题+正文 lark_md+链接按钮）、巡检报告/日报推送。
    限制：受 BINGOPS_MCP_WRITE_ENABLED 总开关控制；target_type=chat 受
    BINGOPS_MCP_FEISHU_ALLOWED_CHATS 白名单收窄；卡片 JSON 体积上限 30KB（超出需精简）；
    卡片内容遵循飞书卡片协议（JSON 1.0/2.0）。
    """
    _ensure_write_enabled()
    if target_type not in ("chat", "user"):
        raise BingOpsError(
            f"unsupported target_type: {target_type} (use chat | user)",
            code=40001, http_status=422,
        )
    _ensure_card_size(card)
    receive_id_type = "chat_id" if target_type == "chat" else "open_id"
    if target_type == "chat":
        _ensure_feishu_chat_allowed(target)

    message_id = await feishu_bot.send_interactive_card(receive_id_type, target, card)
    return {
        "sent": True,
        "message_id": message_id,
        "target_type": target_type,
        "target": brief(target, 32),
    }


@mcp.tool()
@mcp_tool_logging("update_feishu_card")
async def update_feishu_card(message_id: str, card: dict) -> dict:
    """更新已发送的飞书卡片内容（流式输出：先发占位卡，随后周期刷新至最终形态）。

    适用场景：长回答的打字机式增量展示、巡检/分析过程中卡片状态流转（进行中→完成）。
    限制：受总开关控制；仅能更新本应用发送的卡片；建议更新间隔 ≥1~2s 且内容有实质
    变化时再调用，避免触发飞书频率限制；卡片体积上限 30KB。
    """
    _ensure_write_enabled()
    _ensure_card_size(card)
    await feishu_bot.update_message(message_id, card)
    return {"updated": True, "message_id": brief(message_id, 48)}
