"""告警事件闭环层 API 路由（webhook + 事件列表 + 统计 + 规则映射）。

契约豁免说明（语义分化）：POST /webhook 为机器对机器接口
（ck-log-alert / 夜莺回调），鉴权走 X-Agent-Token 静态 token，
不绑定用户 JWT/权限码；响应仍使用平台统一信封。
其余端点为管理面，走标准 require_permission。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.api.dependencies import get_db_session, require_permission
from bingops.core.config import settings
from bingops.core.exceptions import AuthenticationError, ValidationError
from bingops.core.response import paginated_response, success_response
from bingops.models.user import User
from bingops.schemas.alert import (
    VALID_GROUP_BYS,
    AlertRuleCreate,
    AlertRuleUpdate,
    AlertWebhookPayload,
    event_to_response,
    rule_to_response,
)
from bingops.services import alert_service

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


# ── Webhook（机器对机器，X-Agent-Token） ─────────────────────────────────────


def _verify_agent_token(agent_token: str | None) -> None:
    """校验 X-Agent-Token；未配置（空）时拒绝全部请求（fail closed）。"""
    if not settings.alert_agent_token:
        raise AuthenticationError(
            "Agent token not configured (BINGOPS_ALERT_AGENT_TOKEN)"
        )
    if agent_token != settings.alert_agent_token:
        raise AuthenticationError("Invalid agent token")


@router.post("/webhook")
async def report_alert_event(
    payload: AlertWebhookPayload,
    x_agent_token: str | None = Header(default=None, alias="X-Agent-Token"),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """告警事件回报入口（所有来源统一收敛）。

    响应 data 为 notify 协议：notify=false 时执行器可跳过本轮飞书通知；
    回报失败/超时时执行器默认发，主路不依赖本平台。
    """
    _verify_agent_token(x_agent_token)
    result = await alert_service.handle_webhook_event(session, payload)
    return success_response(data=result, message="ok")


# ── 事件列表 ─────────────────────────────────────────────────────────────────


@router.get("/events")
async def list_alert_events(
    status: str | None = Query(None, description="firing|resolved|error"),
    source: str | None = None,
    rule_code: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("alert:list"),
) -> dict:
    """告警事件分页列表（按 first_seen_at 过滤区间）。"""
    from bingops.repositories.alert_repo import AlertEventRepo

    items, total = await AlertEventRepo(session).list(
        status=status,
        source=source,
        rule_code=rule_code,
        since=since,
        until=until,
        page=page,
        page_size=page_size,
    )
    return paginated_response(
        items=[event_to_response(event) for event in items],
        total=total,
        page=page,
        page_size=page_size,
    )


# ── 统计 ─────────────────────────────────────────────────────────────────────


@router.get("/stats/summary")
async def alert_stats_summary(
    group_by: str = Query("source", description="source|rule_code|group_id|day"),
    since: datetime | None = None,
    until: datetime | None = None,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("alert:list"),
) -> dict:
    """告警统计：各分组状态计数、当前活跃 firing 总数、平均恢复时长。

    高频 firing 规则排名即噪声规则治理清单。
    """
    if group_by not in VALID_GROUP_BYS:
        raise ValidationError(f"group_by must be one of: {VALID_GROUP_BYS}")
    summary = await alert_service.stats_summary(
        session, group_by=group_by, since=since, until=until,
    )
    return success_response(data=summary)


# ── 规则映射 CRUD ────────────────────────────────────────────────────────────


@router.get("/rules")
async def list_alert_rules(
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("alert:list"),
) -> dict:
    """规则映射列表（rule_code → 处理组 / stale 窗口 / 静态 labels）。"""
    rules = await alert_service.list_rules(session)
    return success_response(data=[rule_to_response(rule) for rule in rules])


@router.post("/rules")
async def create_alert_rule(
    payload: AlertRuleCreate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("alert:create"),
) -> dict:
    """创建规则映射。code 须与执行器侧 rule_code 对齐（人工纪律）。"""
    rule = await alert_service.create_rule(session, payload)
    return success_response(data=rule_to_response(rule), message="created")


@router.put("/rules/{rule_id}")
async def update_alert_rule(
    rule_id: int,
    payload: AlertRuleUpdate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("alert:update"),
) -> dict:
    """更新规则映射。"""
    rule = await alert_service.update_rule(session, rule_id, payload)
    return success_response(data=rule_to_response(rule))


@router.delete("/rules/{rule_id}")
async def delete_alert_rule(
    rule_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("alert:delete"),
) -> dict:
    """删除规则映射（事件保留，仅停止开单联动）。"""
    await alert_service.delete_rule(session, rule_id)
    return success_response(message="deleted")
