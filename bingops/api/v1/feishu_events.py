"""飞书事件回调端点（入站链路）。

接收飞书开放平台推送的事件（im.message.receive_v1 等）与卡片交互回调，
以及事件订阅 URL 保存时的 challenge 验证握手。

安全模型（对齐告警 webhook 的 fail-closed 先例）：
- Verification Token / Encrypt Key 未配置 → 拒绝全部回调（401）
- 报文带 encrypt 字段时按 AES-256-CBC 解密（key = SHA256(Encrypt Key)，iv = 密文前 16 字节）
- token 校验不过 → 401

当前为最小骨架：challenge 回显 + 事件留痕日志；建单等业务逻辑待后续迭代。
纪律：必须秒级 ack（飞书对超时回调重推），事件消费按 event_id 幂等（后续迭代实现）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from bingops.core.config import feishu_settings

logger = logging.getLogger(f"bingops.{__name__}")

router = APIRouter(prefix="/api/v1/integrations/feishu", tags=["integrations"])


def _decrypt_payload(encrypt_b64: str) -> dict:
    """按飞书约定解密回调报文：AES-256-CBC，key=SHA256(Encrypt Key)，iv=密文前 16 字节。"""
    if not feishu_settings.event_encrypt_key:
        raise ValueError("encrypt key not configured")
    key = hashlib.sha256(feishu_settings.event_encrypt_key.encode()).digest()
    raw = base64.b64decode(encrypt_b64)
    decryptor = Cipher(algorithms.AES(key), modes.CBC(raw[:16])).decryptor()
    padded = decryptor.update(raw[16:]) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    plain = unpadder.update(padded) + unpadder.finalize()
    return json.loads(plain)


def _verify_token(token: str | None) -> bool:
    """校验回调报文携带的 Verification Token（fail-closed：未配置即拒绝）。"""
    expected = feishu_settings.event_verification_token
    return bool(expected) and token == expected


@router.post("/events")
async def feishu_events(request: Request) -> JSONResponse:
    """飞书事件 / 卡片交互回调统一入口。

    三类报文：url_verification 握手、加密事件（{"encrypt": ...}）、明文事件。
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"code": 400, "msg": "invalid json"})

    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"code": 400, "msg": "invalid body"})

    # 加密封：先解出内层报文（解密成功本身即持有 Encrypt Key 的证明）
    if "encrypt" in body:
        try:
            data = _decrypt_payload(body["encrypt"])
        except Exception:
            logger.warning("Feishu callback decrypt failed")
            return JSONResponse(status_code=401, content={"code": 401, "msg": "decrypt failed"})
    else:
        data = body

    # URL 验证握手：1 秒内原样回显 challenge
    if data.get("type") == "url_verification" or "challenge" in data:
        if not _verify_token(data.get("token")):
            logger.warning("Feishu url_verification token mismatch")
            return JSONResponse(status_code=401, content={"code": 401, "msg": "invalid token"})
        logger.info("Feishu url_verification passed")
        return {"challenge": data.get("challenge", "")}

    # 普通事件 / 卡片回调：v2 报文 token 在 header，v1 兼容在顶层
    token = (data.get("header") or {}).get("token") or data.get("token")
    if not _verify_token(token):
        logger.warning("Feishu callback token mismatch")
        return JSONResponse(status_code=401, content={"code": 401, "msg": "invalid token"})

    header = data.get("header") or {}
    event = data.get("event") or {}
    logger.info(
        "Feishu callback received",
        extra={
            "event_type": header.get("event_type") or data.get("type"),
            "event_id": header.get("event_id"),
            "keys": sorted(data.keys()),
            "has_message": "message" in event,
        },
    )
    # 骨架阶段：只 ack 不处理；业务逻辑（私聊建单 / 卡片交互）按 event_id 幂等接入
    return {"code": 0}
