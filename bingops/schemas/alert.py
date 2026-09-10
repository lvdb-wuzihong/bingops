"""告警事件闭环层 Pydantic 模型（契约见 docs/monitoring-design.md §4）。

Webhook 契约豁免说明：本端点为机器对机器接口（ck-log-alert / 夜莺回调），
鉴权走 X-Agent-Token 静态 token 而非用户 JWT；响应仍使用平台统一信封。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from bingops.models.alert import AlertEvent, AlertRule, MonitoringSource, NotifyChannel

VALID_SOURCES = ("ck-log-alert", "n9e", "bingops")
# 平台原生规则的默认告警来源标识（夜莺退役后新规则统一用此值）
DEFAULT_RULE_SOURCE = "bingops"
VALID_STATUSES = ("firing", "resolved", "error")
VALID_SEVERITIES = (1, 2, 3)
VALID_GROUP_BYS = ("source", "rule_code", "group_id", "day")
VALID_SOURCE_TYPES = ("clickhouse", "victoria", "prometheus")
VALID_CHANNEL_TYPES = ("feishu_webhook",)
# 凭据约定：password_ref/secret_ref 填此值 = 数据源无认证（执行器连接时不带凭据）；
# 仅限内网/白名单可达的数据源使用，加认证后应改为 env 变量名
NO_AUTH_REF = "NO_AUTH"


# ── Webhook 契约 ──────────────────────────────────────────────────────────────


class AlertWebhookPayload(BaseModel):
    """告警事件回报请求体。

    resolved 仅由自带恢复语义的来源（夜莺）直传；
    ck-log-alert 一期只报 firing/error，恢复由平台 stale 超时推导。
    """

    source: str = Field(min_length=1, max_length=32, description="来源标识")
    rule_code: str = Field(min_length=1, max_length=128, description="规则稳定聚合键")
    rule_name: str | None = Field(default=None, max_length=255)
    status: Literal["firing", "resolved", "error"]
    window_start: datetime | None = None
    window_end: datetime | None = None
    total_count: int | None = Field(default=None, ge=0)
    severity: int | None = Field(default=None, description="1严重 2中等 3轻微")
    labels: dict = Field(default_factory=dict)
    details: Any = None  # JSONB 黑盒，平台不解析内部结构
    error: str | None = None

    @model_validator(mode="after")
    def _check_payload(self) -> AlertWebhookPayload:
        if self.severity is not None and self.severity not in VALID_SEVERITIES:
            raise ValueError(f"severity must be one of: {VALID_SEVERITIES}")
        if self.status == "firing" and self.total_count is None:
            raise ValueError("total_count is required for firing events")
        return self


class WebhookResultData(BaseModel):
    """webhook 响应 data（notify 协议）。

    notify=false 表示平台已处理过该活跃 firing，执行器可跳过本轮飞书通知；
    执行器在回报失败/超时/不可解析时默认发（主路退化为现状行为，宁多勿漏）。
    """

    event_id: int | None = None
    notify: bool = True
    suppress_reason: str | None = None
    repeat_after_minutes: int | None = None


# ── 规则映射 DTO ──────────────────────────────────────────────────────────────


class AlertRuleCreate(BaseModel):
    source: str = Field(min_length=1, max_length=32)
    code: str = Field(min_length=1, max_length=128)
    name: str | None = Field(default=None, max_length=255)
    group_id: int | None = None
    stale_minutes: int = Field(default=5, ge=1)
    default_severity: int = 2
    static_labels: dict = Field(default_factory=dict)
    notify_enabled: bool = True
    enabled: bool = True
    # ── 二期：评估契约字段（分发源） ──
    source_id: int | None = Field(default=None, description="绑定监控数据源 ID")
    eval_sql: str | None = Field(
        default=None,
        description="评估 SQL，契约：单行两列 error_count + log_details；含 {window_minutes} 占位",
    )
    threshold: int = Field(default=1, ge=1)
    interval_minutes: int = Field(default=1, ge=1)
    for_rounds: int = Field(default=1, ge=1, description="连续 M 轮达标才报 firing（防抖）")
    detail_limit: int = Field(default=10, ge=1)
    grafana_url: str | None = None
    feishu_card_template: dict | None = None
    notify_channel_id: int | None = Field(
        default=None, description="绑定通知渠道（空 = 通知由执行器默认处理）",
    )


class AlertRuleUpdate(BaseModel):
    name: str | None = None
    group_id: int | None = None
    stale_minutes: int | None = Field(default=None, ge=1)
    default_severity: int | None = None
    static_labels: dict | None = None
    notify_enabled: bool | None = None
    enabled: bool | None = None
    source_id: int | None = None
    eval_sql: str | None = None
    threshold: int | None = Field(default=None, ge=1)
    interval_minutes: int | None = Field(default=None, ge=1)
    for_rounds: int | None = Field(default=None, ge=1)
    detail_limit: int | None = Field(default=None, ge=1)
    grafana_url: str | None = None
    feishu_card_template: dict | None = None
    notify_channel_id: int | None = None


class AlertRuleResponse(BaseModel):
    id: int
    source: str
    code: str
    name: str | None
    group_id: int | None
    stale_minutes: int
    default_severity: int
    static_labels: dict
    notify_enabled: bool
    enabled: bool
    source_id: int | None
    eval_sql: str | None
    threshold: int
    interval_minutes: int
    for_rounds: int
    detail_limit: int
    grafana_url: str | None
    feishu_card_template: dict | None
    notify_channel_id: int | None
    created_at: datetime
    updated_at: datetime


# ── 事件 DTO ─────────────────────────────────────────────────────────────────


class AlertEventResponse(BaseModel):
    id: int
    source: str
    rule_code: str
    rule_name: str | None
    status: str
    window_start: datetime | None
    window_end: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None
    resolve_reason: str | None
    total_count: int
    severity: int
    labels: dict
    resource_ids: list
    details: Any | None
    error: str | None
    ticket_id: int | None
    group_id: int | None
    created_at: datetime
    updated_at: datetime


def event_to_response(event: AlertEvent) -> dict:
    """ORM 事件转响应字典。"""
    return AlertEventResponse(
        id=event.id,
        source=event.source,
        rule_code=event.rule_code,
        rule_name=event.rule_name,
        status=event.status,
        window_start=event.window_start,
        window_end=event.window_end,
        first_seen_at=event.first_seen_at,
        last_seen_at=event.last_seen_at,
        resolved_at=event.resolved_at,
        resolve_reason=event.resolve_reason,
        total_count=event.total_count,
        severity=event.severity,
        labels=event.labels or {},
        resource_ids=event.resource_ids or [],
        details=event.details,
        error=event.error,
        ticket_id=event.ticket_id,
        group_id=event.group_id,
        created_at=event.created_at,
        updated_at=event.updated_at,
    ).model_dump(mode="json")


def rule_to_response(rule: AlertRule) -> dict:
    """ORM 规则映射转响应字典。"""
    return AlertRuleResponse(
        id=rule.id,
        source=rule.source,
        code=rule.code,
        name=rule.name,
        group_id=rule.group_id,
        stale_minutes=rule.stale_minutes,
        default_severity=rule.default_severity,
        static_labels=rule.static_labels or {},
        notify_enabled=rule.notify_enabled,
        enabled=rule.enabled,
        source_id=rule.source_id,
        eval_sql=rule.eval_sql,
        threshold=rule.threshold,
        interval_minutes=rule.interval_minutes,
        for_rounds=rule.for_rounds,
        detail_limit=rule.detail_limit,
        grafana_url=rule.grafana_url,
        feishu_card_template=rule.feishu_card_template,
        notify_channel_id=rule.notify_channel_id,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    ).model_dump(mode="json")


# ── 监控数据源 DTO ───────────────────────────────────────────────────────────


class MonitoringSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    type: Literal["clickhouse", "victoria", "prometheus"]
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    database_name: str | None = Field(default=None, max_length=64)
    username: str | None = Field(default=None, max_length=64)
    password_ref: str = Field(
        min_length=1, max_length=128,
        description="凭据引用名（执行器侧 env 变量名，平台不落密码）；"
                    "数据源无认证时填 NO_AUTH（仅限内网/白名单可达）",
    )
    secure: bool = False
    enabled: bool = True


class MonitoringSourceUpdate(BaseModel):
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    database_name: str | None = None
    username: str | None = None
    password_ref: str | None = Field(default=None, max_length=128)
    secure: bool | None = None
    enabled: bool | None = None


class MonitoringSourceResponse(BaseModel):
    id: int
    name: str
    type: str
    host: str
    port: int
    database_name: str | None
    username: str | None
    password_ref: str
    secure: bool
    enabled: bool
    created_at: datetime
    updated_at: datetime


def source_to_response(src: MonitoringSource) -> dict:
    """ORM 数据源转响应字典。"""
    return MonitoringSourceResponse(
        id=src.id,
        name=src.name,
        type=src.type,
        host=src.host,
        port=src.port,
        database_name=src.database_name,
        username=src.username,
        password_ref=src.password_ref,
        secure=src.secure,
        enabled=src.enabled,
        created_at=src.created_at,
        updated_at=src.updated_at,
    ).model_dump(mode="json")


# ── Agent 分发契约（executor 拉取；凭据只带引用名） ────────────────────────────


class AgentSourceConfig(BaseModel):
    """分发体中的数据源（非敏感连接参数 + password_ref 引用）。"""

    name: str
    type: str
    host: str
    port: int
    database_name: str | None
    username: str | None
    password_ref: str
    secure: bool


class AgentNotifyChannel(BaseModel):
    """分发体中的通知渠道（secret_ref 引用，发送动作在执行器）。"""

    name: str
    type: str
    secret_ref: str
    extra: dict


class AgentRuleConfig(BaseModel):
    """分发体中的单条启用规则。"""

    id: int
    source: str = Field(description="告警来源标识，执行器回报事件时原样带回")
    code: str
    name: str | None
    interval_minutes: int
    threshold: int
    for_rounds: int
    detail_limit: int
    eval_sql: str | None
    stale_minutes: int
    default_severity: int
    group_id: int | None
    static_labels: dict
    grafana_url: str | None
    feishu_card_template: dict | None
    notify_enabled: bool
    datasource: AgentSourceConfig | None = Field(
        description="绑定数据源配置；回报事件的 source 取上方告警来源标识",
    )
    notify_channel: AgentNotifyChannel | None = None


class AgentConfigResponse(BaseModel):
    """GET /api/v1/alerts/agent/config 响应体。"""

    rules: list[AgentRuleConfig]


# ── 通知渠道 DTO ─────────────────────────────────────────────────────────────


class NotifyChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    type: Literal["feishu_webhook"]
    secret_ref: str = Field(
        min_length=1, max_length=128,
        description="webhook URL 凭据引用名（URL 含 secret，平台不落真地址）",
    )
    extra: dict = Field(default_factory=dict, description="非敏感参数（@手机号列表等）")
    enabled: bool = True


class NotifyChannelUpdate(BaseModel):
    type: Literal["feishu_webhook"] | None = None
    secret_ref: str | None = Field(default=None, max_length=128)
    extra: dict | None = None
    enabled: bool | None = None


class NotifyChannelResponse(BaseModel):
    id: int
    name: str
    type: str
    secret_ref: str
    extra: dict
    enabled: bool
    created_at: datetime
    updated_at: datetime


def channel_to_response(channel: NotifyChannel) -> dict:
    """ORM 通知渠道转响应字典。"""
    return NotifyChannelResponse(
        id=channel.id,
        name=channel.name,
        type=channel.type,
        secret_ref=channel.secret_ref,
        extra=channel.extra or {},
        enabled=channel.enabled,
        created_at=channel.created_at,
        updated_at=channel.updated_at,
    ).model_dump(mode="json")
