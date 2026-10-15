"""飞书应用机器人消息客户端（平台唯一出站通道）。

复用飞书 SSO 同一自建应用（FeishuSettings.app_id / app_secret），以应用身份
（tenant_access_token）调用 IM API 发送消息；agent/编排层的出站一律经此层，
不持有 app_secret（事件拓扑见编排层 docs/project-charter.md §1.1）。

飞书侧前置条件：
1. 应用开通「机器人」能力；
2. 开通 im:message:send_as_bot（以应用的身份发消息）权限并发布版本；
3. 接收人在应用可用范围内；群聊（chat_id）场景机器人需已在群内；
4. 更新卡片（流式输出）依赖同一消息权限域，若飞书侧拒绝需核对「更新应用发送的消息」权限项。
"""

from __future__ import annotations

import json
import logging
import time

import httpx

from bingops.core.config import feishu_settings
from bingops.core.exceptions import ExternalServiceError, ValidationError

logger = logging.getLogger(f"bingops.{__name__}")

TENANT_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
MESSAGE_SEND_URL = "https://open.feishu.cn/open-apis/im/v1/messages"
# 更新已发送消息（卡片）用；仅能更新本应用发送的消息
MESSAGE_UPDATE_URL_TEMPLATE = "https://open.feishu.cn/open-apis/im/v1/messages/{message_id}"

_HTTP_TIMEOUT = 10.0
# tenant_access_token 进程内缓存：飞书侧有效期 2h，提前 60s 刷新
_token_cache: tuple[str, float] | None = None  # (token, 过期时刻 monotonic)
_TOKEN_REFRESH_MARGIN = 60.0


async def _get_tenant_access_token() -> str:
    """获取应用身份 tenant_access_token（带进程内缓存）。"""
    global _token_cache
    if _token_cache is not None:
        token, expires_at = _token_cache
        if time.monotonic() < expires_at:
            return token

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            TENANT_TOKEN_URL,
            json={
                "app_id": feishu_settings.app_id,
                "app_secret": feishu_settings.app_secret,
            },
            timeout=_HTTP_TIMEOUT,
        )
        data = resp.json()

    if data.get("code") != 0:
        logger.error("Failed to get tenant_access_token", extra={"response": data})
        raise ExternalServiceError("feishu", "Failed to get tenant_access_token")

    token = data["tenant_access_token"]
    expire_seconds = float(data.get("expire", 7200))
    _token_cache = (token, time.monotonic() + expire_seconds - _TOKEN_REFRESH_MARGIN)
    return token


async def _post_message(receive_id_type: str, receive_id: str, msg_type: str, content: str) -> str:
    """以应用身份调 IM API 发消息（receive_id_type: open_id | chat_id），返回 message_id。"""
    token = await _get_tenant_access_token()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            MESSAGE_SEND_URL,
            params={"receive_id_type": receive_id_type},
            headers={"Authorization": f"Bearer {token}"},
            json={
                "receive_id": receive_id,
                "msg_type": msg_type,
                # 飞书契约：content 为消息体 JSON 的二次序列化字符串
                "content": content,
            },
            timeout=_HTTP_TIMEOUT,
        )
        data = resp.json()

    if data.get("code") != 0:
        logger.error(
            "Failed to send feishu message",
            extra={"receive_id_type": receive_id_type, "receive_id": receive_id, "response": data},
        )
        raise ExternalServiceError("feishu", "Failed to send im message")

    message_id = str((data.get("data") or {}).get("message_id", ""))
    logger.info(
        "Feishu message sent", extra={"receive_id_type": receive_id_type, "message_id": message_id},
    )
    return message_id


async def send_interactive(open_id: str, card: dict) -> str:
    """向指定用户（open_id 定位）发送交互卡片私聊消息，返回 message_id。"""
    return await _post_message(
        "open_id", open_id, "interactive", json.dumps(card, ensure_ascii=False),
    )


async def send_interactive_to_chat(chat_id: str, card: dict) -> str:
    """向指定群（chat_id 定位）发送交互卡片（前置：机器人已在群内），返回 message_id。"""
    return await _post_message(
        "chat_id", chat_id, "interactive", json.dumps(card, ensure_ascii=False),
    )


async def send_interactive_card(target_type: str, target_id: str, card: dict) -> str:
    """通用卡片发送（target_type: open_id | chat_id），返回 message_id 供后续更新。"""
    if target_type not in ("open_id", "chat_id"):
        raise ValidationError(f"unsupported target_type: {target_type}")
    return await _post_message(
        target_type, target_id, "interactive", json.dumps(card, ensure_ascii=False),
    )


async def update_message(message_id: str, card: dict) -> None:
    """更新已发送的卡片消息（流式输出用）；仅能更新本应用发送的消息。

    飞书契约：PATCH body 仅需 content（卡片 JSON 的二次序列化字符串），不重传 msg_type。
    """
    token = await _get_tenant_access_token()

    async with httpx.AsyncClient() as client:
        resp = await client.patch(
            MESSAGE_UPDATE_URL_TEMPLATE.format(message_id=message_id),
            headers={"Authorization": f"Bearer {token}"},
            json={"content": json.dumps(card, ensure_ascii=False)},
            timeout=_HTTP_TIMEOUT,
        )
        data = resp.json()

    if data.get("code") != 0:
        logger.error(
            "Failed to update feishu message",
            extra={"message_id": message_id, "response": data},
        )
        raise ExternalServiceError("feishu", "Failed to update im message")

    logger.info("Feishu message updated", extra={"message_id": message_id})


async def send_text(target_type: str, target_id: str, text: str) -> str:
    """发送文本消息（target_type: open_id | chat_id）；供 MCP 写工具等通用场景。"""
    if target_type not in ("open_id", "chat_id"):
        raise ValidationError(f"unsupported target_type: {target_type}")
    return await _post_message(
        target_type, target_id, "text", json.dumps({"text": text}, ensure_ascii=False),
    )
