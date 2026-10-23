"""工单附件业务服务（元数据入库 + OSS 私有桶存储）。

三道约束：
- 状态闸：仅 open / pending_approval / in_progress 可增删；
  终态（resolved/closed/cancelled）只读防篡改。
- 类型与体积闸：扩展名白名单 + 单文件上限（BINGOPS_TICKET_ATTACHMENT_MAX_MB）。
- 所有权闸：删除仅限上传者本人或超管。
"""

from __future__ import annotations

import logging
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from bingops.core import oss_client
from bingops.core.config import settings
from bingops.core.exceptions import NotFoundError, PermissionDeniedError, ValidationError
from bingops.models.ticket import Ticket, TicketAttachment
from bingops.models.user import User
from bingops.repositories.ticket_attachment_repo import TicketAttachmentRepo

logger = logging.getLogger(f"bingops.{__name__}")

# 允许的附件扩展名（截图 / 文本日志 / 计划文档 / 脚本包）
ALLOWED_EXTENSIONS = frozenset({
    "png", "jpg", "jpeg", "gif", "webp", "bmp",
    "txt", "log", "json", "csv", "md", "yaml", "yml", "sql",
    "pdf", "zip", "gz", "tgz", "tar",
})
# 可变更状态才允许增删附件；终态只读
ACTIVE_ATTACHMENT_STATUSES = ("open", "pending_approval", "in_progress")
MAX_ATTACHMENTS_PER_TICKET = 50


def build_download_url(ticket_id: int, attachment_id: int) -> str:
    """平台代理下载地址（前端直接使用，不接触 OSS）。"""
    return f"/api/v1/tickets/{ticket_id}/attachments/{attachment_id}/download"


def _extension_of(filename: str) -> str:
    _, _, ext = (filename or "").rpartition(".")
    return ext.lower().strip()


async def list_attachments(
    session: AsyncSession, ticket_id: int,
) -> list[tuple[TicketAttachment, str | None]]:
    """列出工单附件（含上传者显示名）。"""
    return await TicketAttachmentRepo(session).list_by_ticket(ticket_id)


async def upload_attachment(
    session: AsyncSession,
    ticket: Ticket,
    *,
    filename: str,
    content_type: str | None,
    data: bytes,
    operator: User,
) -> TicketAttachment:
    """上传附件：校验 → OSS 落盘 → 元数据入库。"""
    if ticket.status not in ACTIVE_ATTACHMENT_STATUSES:
        raise ValidationError(
            f"attachments are read-only in status '{ticket.status}'",
        )

    clean_name = (filename or "").strip()
    if not clean_name:
        raise ValidationError("filename is required")
    if len(clean_name) > 256:
        raise ValidationError("filename too long (max 256)")

    ext = _extension_of(clean_name)
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            f"file type '.{ext}' is not allowed; allowed: {sorted(ALLOWED_EXTENSIONS)}",
        )

    max_bytes = settings.ticket_attachment_max_mb * 1024 * 1024
    if not data:
        raise ValidationError("empty file")
    if len(data) > max_bytes:
        raise ValidationError(
            f"file too large: {len(data)} bytes (max {settings.ticket_attachment_max_mb} MB)",
        )

    repo = TicketAttachmentRepo(session)
    existing = await repo.count_by_ticket(ticket.id)
    if existing >= MAX_ATTACHMENTS_PER_TICKET:
        raise ValidationError(
            f"too many attachments on this ticket (max {MAX_ATTACHMENTS_PER_TICKET})",
        )

    key = oss_client.build_object_key(ticket.id, uuid4().hex, clean_name)
    await oss_client.put_object(key, data, content_type)

    attachment = TicketAttachment(
        ticket_id=ticket.id,
        filename=clean_name,
        size=len(data),
        content_type=content_type,
        oss_key=key,
        uploaded_by=operator.id,
    )
    attachment = await repo.create(attachment)
    await session.commit()

    logger.info(
        "Ticket attachment uploaded",
        extra={
            "ticket_id": ticket.id,
            "attachment_id": attachment.id,
            "size": attachment.size,
            "user_id": operator.id,
        },
    )
    return attachment


async def get_attachment_for_download(
    session: AsyncSession, ticket_id: int, attachment_id: int,
) -> tuple[TicketAttachment, bytes]:
    """读取附件内容（供平台代理下载端点流式输出）。"""
    attachment = await TicketAttachmentRepo(session).get_by_id(attachment_id)
    if attachment is None or attachment.ticket_id != ticket_id:
        raise NotFoundError("TicketAttachment", str(attachment_id))

    data = await oss_client.get_object(attachment.oss_key)
    return attachment, data


async def delete_attachment(
    session: AsyncSession,
    ticket: Ticket,
    attachment_id: int,
    operator: User,
) -> None:
    """删除附件：仅上传者或超管，且工单处于可变更状态。"""
    repo = TicketAttachmentRepo(session)
    attachment = await repo.get_by_id(attachment_id)
    if attachment is None or attachment.ticket_id != ticket.id:
        raise NotFoundError("TicketAttachment", str(attachment_id))

    if ticket.status not in ACTIVE_ATTACHMENT_STATUSES:
        raise ValidationError(
            f"attachments are read-only in status '{ticket.status}'",
        )
    if attachment.uploaded_by != operator.id and not operator.is_superuser:
        raise PermissionDeniedError("Only the uploader or admin can delete this attachment")

    # 先删对象再删元数据：对象不存在视为成功，避免"库有记录、桶无对象"的悬空状态
    await oss_client.delete_object(attachment.oss_key)
    await repo.delete(attachment)
    await session.commit()

    logger.info(
        "Ticket attachment deleted",
        extra={
            "ticket_id": ticket.id,
            "attachment_id": attachment_id,
            "user_id": operator.id,
        },
    )
