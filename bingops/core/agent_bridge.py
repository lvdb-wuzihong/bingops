"""Agent 编排层桥接：飞书入站消息向编排层 HTTP 转发。

事件拓扑（编排层 docs/project-charter.md §1.1）：飞书事件唯一入口在平台 webhook
（feishu_events.py 已验签解密），命中 agent 分流（/ai 前缀）的消息由平台转发编排层——
编排层不直连飞书、不验签、不持有 app_secret。

纪律：fire-and-forget（调用方 asyncio.create_task），失败只记日志不阻断事件秒级 ack。
"""

from __future__ import annotations

import logging

import httpx

from bingops.core.config import settings

logger = logging.getLogger(f"bingops.{__name__}")

_HTTP_TIMEOUT = 5.0


async def forward_agent_message(payload: dict) -> None:
    """转发消息事件到编排层；未配置 BINGOPS_AGENT_CALLBACK_URL 时静默跳过。

    payload 契约（编排层接收端点约定）：event_type / sender_open_id / chat_id / text。
    """
    url = settings.agent_callback_url.strip()
    if not url:
        logger.debug("Agent callback URL not configured, skip forwarding")
        return

    headers = (
        {"X-Agent-Token": settings.agent_callback_token} if settings.agent_callback_token else {}
    )
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=headers, timeout=_HTTP_TIMEOUT)

    if resp.status_code >= 400:
        logger.warning(
            "Agent callback failed",
            extra={"url": url, "status_code": resp.status_code},
        )
