"""飞书入站事件业务处理：私聊建单（一期）。

入口收敛为两类报文（均已经 feishu_events.py 验签 / 解密）：
- im.message.receive_v1：单聊文本 → 识别「建单」指令 → 回建单表单卡片
- card.action.trigger：表单提交 → create_ticket → toast + 确认卡片

纪律：
- 秒级 ack：处理保持轻量（本地 DB 查询 + 出站发卡片一律 fire-and-forget）
- 幂等：按 event_id 去重（进程内 LRU；飞书对超时回调会重推）
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bingops.api.dependencies import has_permissions
from bingops.core import feishu_bot
from bingops.core.config import settings
from bingops.core.exceptions import PermissionDeniedError, ValidationError
from bingops.models.ticket import TicketCatalog
from bingops.models.user import User
from bingops.schemas.ticket import TicketCreate
from bingops.services import ticket_service

logger = logging.getLogger(f"bingops.{__name__}")

# 触发建单的关键词（机器人菜单「创建工单」的预设文本即「建单」）
CREATE_TICKET_KEYWORDS = ("建单", "创建工单", "建工单")
VALID_PRIORITIES = ("low", "medium", "high", "urgent")
PRIORITY_LABELS = {"low": "低", "medium": "中", "high": "高", "urgent": "紧急"}

# event_id 去重（飞书超时重推；进程内 LRU，容量上限防泄漏）
_DEDUP_CAP = 2000
_event_dedup: OrderedDict[str, None] = OrderedDict()

# fire-and-forget 发送任务的强引用，防止任务被 GC 半路丢弃
_send_tasks: set[asyncio.Task] = set()


def is_duplicate(event_id: str | None) -> bool:
    """event_id 去重：重复回调直接跳过，防止重推导致重复建单。"""
    if not event_id:
        return False
    if event_id in _event_dedup:
        _event_dedup.move_to_end(event_id)
        return True
    _event_dedup[event_id] = None
    if len(_event_dedup) > _DEDUP_CAP:
        _event_dedup.popitem(last=False)
    return False


def _toast(toast_type: str, content: str) -> dict:
    """卡片回调响应体：toast 提示（飞书原位弹出，不改卡片内容）。"""
    return {"toast": {"type": toast_type, "content": content}}


async def _get_user_by_open_id(session: AsyncSession, open_id: str) -> User | None:
    if not open_id:
        return None
    result = await session.execute(select(User).where(User.feishu_open_id == open_id))
    return result.scalar_one_or_none()


async def _list_active_catalog_items(session: AsyncSession) -> list[TicketCatalog]:
    """活跃二级事项（卡片下拉选项，名称 = 一级分类/二级事项）。"""
    result = await session.execute(
        select(TicketCatalog)
        .options(selectinload(TicketCatalog.parent))
        .where(TicketCatalog.parent_id.isnot(None), TicketCatalog.is_active.is_(True))
        .order_by(TicketCatalog.id)
        .limit(100),
    )
    return list(result.scalars().all())


async def _send_card(open_id: str, card: dict, *, scene: str) -> None:
    """fire-and-forget 私聊发卡片：杜绝出站调用阻塞回调响应。"""

    async def _send() -> None:
        try:
            await feishu_bot.send_interactive(open_id, card)
        except Exception:
            logger.exception("Feishu card send failed", extra={"scene": scene})

    task = asyncio.create_task(_send(), name=f"feishu-card-{scene}")
    _send_tasks.add(task)
    task.add_done_callback(_send_tasks.discard)


def _item_label(item: TicketCatalog) -> str:
    parent_name = item.parent.name if item.parent else ""
    return f"{parent_name}/{item.name}" if parent_name else item.name


# ── 消息分支：im.message.receive_v1 ──────────────────────────────────────


async def handle_message_event(session: AsyncSession, data: dict) -> None:
    """单聊文本识别建单指令，回建单表单卡片；其他消息静默忽略。"""
    event = data.get("event") or {}
    message = event.get("message") or {}
    open_id = ((event.get("sender") or {}).get("sender_id") or {}).get("open_id", "")

    # 一期仅支持单聊；群聊 @ 场景二期处理
    if message.get("chat_type") != "p2p" or message.get("message_type") != "text":
        return

    try:
        text = str(json.loads(message.get("content") or "{}").get("text", "")).strip()
    except json.JSONDecodeError:
        return
    if not any(text == kw or text.startswith(kw) for kw in CREATE_TICKET_KEYWORDS):
        return

    user = await _get_user_by_open_id(session, open_id)
    if user is None or not user.is_active:
        # 飞书账号未绑定平台：仍可按 open_id 回复引导卡片（登录一次即自动绑定）
        logger.info("Feishu ticket create skipped: identity not bound", extra={"open_id": open_id})
        await _send_card(open_id, _build_guide_card(), scene="guide")
        return

    items = await _list_active_catalog_items(session)
    await _send_card(user.feishu_open_id, _build_create_form_card(items), scene="create-form")
    logger.info(
        "Feishu create form sent",
        extra={"open_id": open_id, "catalog_options": len(items)},
    )


# ── 卡片回调分支：card.action.trigger ────────────────────────────────────


async def handle_card_action(session: AsyncSession, data: dict) -> dict:
    """表单提交 → create_ticket；响应体为 toast（飞书原位提示）。"""
    event = data.get("event") or {}
    form_value = (event.get("action") or {}).get("form_value") or {}
    open_id = (event.get("operator") or {}).get("open_id", "")

    user = await _get_user_by_open_id(session, open_id)
    if user is None or not user.is_active:
        return _toast("error", "平台账号未绑定或已禁用")
    if not has_permissions(user, "ticket:create"):
        return _toast("error", "无建单权限（ticket:create）")

    title = str(form_value.get("title") or "").strip()
    if not title:
        return _toast("error", "标题不能为空")
    try:
        catalog_item_id = int(form_value.get("catalog_item_id"))
    except (TypeError, ValueError):
        return _toast("error", "请选择服务目录事项")
    priority = str(form_value.get("priority") or "medium")
    if priority not in VALID_PRIORITIES:
        priority = "medium"
    description = str(form_value.get("description") or "").strip() or None

    payload = TicketCreate(
        title=title[:256],
        description=description,
        priority=priority,
        catalog_item_id=catalog_item_id,
    )
    try:
        ticket = await ticket_service.create_ticket(session, payload, user)
    except ValidationError as exc:
        return _toast("error", getattr(exc, "message", "工单校验失败"))
    except PermissionDeniedError as exc:
        return _toast("error", getattr(exc, "message", "无权限"))

    await _send_card(
        user.feishu_open_id, _build_created_card(ticket, user), scene="created",
    )
    logger.info(
        "Feishu ticket created",
        extra={"ticket_id": ticket.id, "ticket_no": ticket.ticket_no, "user_id": user.id},
    )
    return _toast("success", f"工单 {ticket.ticket_no} 已创建")


# ── 卡片构建 ─────────────────────────────────────────────────────────────


def _form_action_url() -> str | None:
    base = settings.ticket_notify_web_base_url.rstrip("/")
    return base or None


def _build_guide_card() -> dict:
    """未绑定用户的引导卡片。"""
    return {
        "schema": "2.0",
        "header": {"title": {"tag": "plain_text", "content": "系统运维平台"}, "template": "blue"},
        "body": {
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": "你的飞书账号尚未绑定平台账号。\n**请先在平台完成一次飞书登录**，绑定后即可在此建单。",
                    },
                },
            ]
        },
    }


def _build_create_form_card(items: list[TicketCatalog]) -> dict:
    """建单表单卡片：目录事项 / 优先级下拉 + 标题 / 描述输入 + 提交按钮。"""
    options = [{"label": _item_label(i), "value": str(i.id)} for i in items]
    elements = [
        {
            "tag": "form",
            "name": "ticket_form",
            "elements": [
                {
                    "tag": "select_static",
                    "name": "catalog_item_id",
                    "label": {"tag": "plain_text", "content": "服务目录事项"},
                    "placeholder": {"tag": "plain_text", "content": "请选择事项"},
                    "options": options,
                },
                {
                    "tag": "select_static",
                    "name": "priority",
                    "label": {"tag": "plain_text", "content": "优先级"},
                    "placeholder": {"tag": "plain_text", "content": "默认为中"},
                    "options": [
                        {"label": PRIORITY_LABELS[p], "value": p} for p in VALID_PRIORITIES
                    ],
                },
                {
                    "tag": "input",
                    "name": "title",
                    "label": {"tag": "plain_text", "content": "标题"},
                    "placeholder": {"tag": "plain_text", "content": "一句话描述问题"},
                    "max_length": 200,
                },
                {
                    "tag": "input",
                    "name": "description",
                    "label": {"tag": "plain_text", "content": "描述（选填）"},
                    "placeholder": {"tag": "plain_text", "content": "补充信息"},
                    "max_length": 2000,
                },
                {
                    "tag": "button",
                    "name": "submit",
                    "text": {"tag": "plain_text", "content": "提交工单"},
                    "type": "primary",
                    "action_type": "form_submit",
                    "form_action_type": "submit",
                },
            ],
        },
    ]
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {"title": {"tag": "plain_text", "content": "新建工单"}, "template": "blue"},
        "body": {"elements": elements},
    }


def _build_created_card(ticket, user: User) -> dict:
    """建单成功确认卡片（复用工单通知的跳转按钮约定）。"""
    elements = [
        {
            "tag": "div",
            "fields": [
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**工单编号**\n{ticket.ticket_no}"}},
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**状态**\n{ticket.status}"}},
            ],
        },
        {"tag": "div", "text": {"tag": "lark_md", "content": f"**标题**\n{ticket.title}"}},
    ]
    base = _form_action_url()
    if base:
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "action",
            "actions": [{
                "tag": "button",
                "text": {"tag": "plain_text", "content": "查看工单"},
                "type": "primary",
                "url": f"{base}/{ticket.id}",
            }],
        })
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "工单已创建"}, "template": "green"},
        "elements": elements,
    }
