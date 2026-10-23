"""工单附件 OSS 客户端（私有桶，平台代理读写）。

oss2 是同步 SDK，所有调用统一走 `asyncio.to_thread` 避免阻塞事件循环。

安全边界：
- 桶为私有，不在客户端暴露签名 URL；下载一律经平台代理端点鉴权后流出。
- 平台自身出站凭据走环境变量（RAM 子账号，最小权限：该桶 Put/Get/Delete），
  与业务密钥（Vault 引用，由 runner 解析）分属不同层。
"""

from __future__ import annotations

import asyncio
import logging

import oss2

from bingops.core.config import settings
from bingops.core.exceptions import ExternalServiceError, NotFoundError

logger = logging.getLogger(f"bingops.{__name__}")

_bucket: oss2.Bucket | None = None


def _get_bucket() -> oss2.Bucket:
    """惰性构造 bucket 句柄；未启用/配置缺失直接 503。"""
    global _bucket
    if not settings.ticket_attachment_enabled:
        raise ExternalServiceError(
            "oss", "attachment storage is disabled (BINGOPS_TICKET_ATTACHMENT_ENABLED)",
            http_status=503,
        )
    missing = [
        name for name, value in (
            ("BINGOPS_OSS_ENDPOINT", settings.oss_endpoint),
            ("BINGOPS_OSS_BUCKET", settings.oss_bucket),
            ("BINGOPS_OSS_ACCESS_KEY_ID", settings.oss_access_key_id),
            ("BINGOPS_OSS_ACCESS_KEY_SECRET", settings.oss_access_key_secret),
        ) if not value
    ]
    if missing:
        raise ExternalServiceError(
            "oss", f"attachment storage not configured: {', '.join(missing)}",
            http_status=503,
        )
    if _bucket is None:
        auth = oss2.Auth(settings.oss_access_key_id, settings.oss_access_key_secret)
        _bucket = oss2.Bucket(auth, settings.oss_endpoint, settings.oss_bucket)
    return _bucket


def build_object_key(ticket_id: int, token: str, filename: str) -> str:
    """对象键：{prefix}/ticket/{ticket_id}/{token}/{filename}

    中间 ``ticket/`` 段是**对象类别命名空间**：业务对象（工单/SQL 快照/巡检报告）
    各自占一个一级目录，RAM 策略可按类别授权而无需整前缀放开。token 为 uuid，
    避免同名覆盖。
    """
    safe_name = filename.replace("/", "_").replace("\\", "_").strip() or "attachment"
    return f"{settings.oss_attachment_prefix}/ticket/{ticket_id}/{token}/{safe_name}"


async def put_object(key: str, data: bytes, content_type: str | None = None) -> None:
    """上传对象（≤ ticket_attachment_max_mb）。"""
    bucket = _get_bucket()
    headers = {"Content-Type": content_type} if content_type else None
    try:
        await asyncio.to_thread(bucket.put_object, key, data, headers=headers)
    except oss2.exceptions.OssError as exc:
        logger.error(
            "OSS put object failed",
            extra={"key": key, "status": getattr(exc, "status", None)},
        )
        raise ExternalServiceError("oss", "failed to store attachment") from exc


async def get_object(key: str) -> bytes:
    """读取对象全部内容（附件体积受上限约束，整读可接受）。"""
    bucket = _get_bucket()
    try:
        result = await asyncio.to_thread(bucket.get_object, key)
        return await asyncio.to_thread(result.read)
    except oss2.exceptions.NoSuchKey as exc:
        raise NotFoundError("AttachmentObject", key) from exc
    except oss2.exceptions.OssError as exc:
        logger.error(
            "OSS get object failed",
            extra={"key": key, "status": getattr(exc, "status", None)},
        )
        raise ExternalServiceError("oss", "failed to read attachment") from exc


async def delete_object(key: str) -> None:
    """删除对象（对象不存在视为成功，保证幂等）。"""
    bucket = _get_bucket()
    try:
        await asyncio.to_thread(bucket.delete_object, key)
    except oss2.exceptions.NoSuchKey:
        return
    except oss2.exceptions.OssError as exc:
        logger.error(
            "OSS delete object failed",
            extra={"key": key, "status": getattr(exc, "status", None)},
        )
        raise ExternalServiceError("oss", "failed to delete attachment") from exc
