"""飞书应用机器人消息客户端。

复用飞书 SSO 同一自建应用（FeishuSettings.app_id / app_secret），以应用身份
（tenant_access_token）调用 IM API 发送单聊消息。

飞书侧前置条件：
1. 应用开通「机器人」能力；
2. 开通 im:message:send_as_bot（以应用的身份发消息）权限并发布版本；
3. 接收人在应用可用范围内。
"""

from __future__ import annotations

import json
import logging
import time

import httpx

from bingops.core.config import feishu_settings
from bingops.core.exceptions import ExternalServiceError

logger = logging.getLogger(f"bingops.{__name__}")

TENANT_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
MESSAGE_SEND_URL = "https://open.feishu.cn/open-apis/im/v1/messages"

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


async def send_interactive(open_id: str, card: dict) -> None:
    """向指定用户（open_id 定位）发送交互卡片私聊消息。"""
    token = await _get_tenant_access_token()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            MESSAGE_SEND_URL,
            params={"receive_id_type": "open_id"},
            headers={"Authorization": f"Bearer {token}"},
            json={
                "receive_id": open_id,
                "msg_type": "interactive",
                # 飞书契约：content 为卡片 JSON 的二次序列化字符串
                "content": json.dumps(card, ensure_ascii=False),
            },
            timeout=_HTTP_TIMEOUT,
        )
        data = resp.json()

    if data.get("code") != 0:
        logger.error(
            "Failed to send feishu message",
            extra={"open_id": open_id, "response": data},
        )
        raise ExternalServiceError("feishu", "Failed to send im message")

    logger.info("Feishu message sent", extra={"open_id": open_id})
