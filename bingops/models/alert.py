"""告警事件闭环层 ORM 模型（设计见 docs/monitoring-design.md）。

alert_events：所有来源（ck-log-alert / 夜莺 webhook）的统一事件表，
firing/resolved 状态机只有 DB 一个事实源，进程重启不丢。
alert_rules：规则映射元数据——哪条规则派给哪个处理组、多久无回报算恢复。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bingops.models.base import Base, BaseMixin


class AlertEvent(BaseMixin, Base):
    """统一告警事件。

    状态机：firing --(resolved 事件直传 或 stale 超时)--> resolved；
    error 为独立旁路行（采集链路健康信号），不进状态机、不开单。
    """

    __tablename__ = "alert_events"
    __table_args__ = (
        Index(
            "uq_alert_active_firing",
            "source",
            "rule_code",
            text("COALESCE(monitoring_source_id, 0)"),
            unique=True,
            postgresql_where=text("status = 'firing'"),
        ),
        Index("idx_alert_events_status_time", "status", "first_seen_at"),
        Index(
            "idx_alert_events_last_seen",
            "last_seen_at",
            postgresql_where=text("status = 'firing'"),
        ),
        Index("idx_alert_events_labels", "labels", postgresql_using="gin"),
        Index("idx_alert_events_kind_time", "rule_kind", "first_seen_at"),
    )

    source: Mapped[str] = mapped_column(String(32), nullable=False)  # ck-log-alert | n9e
    rule_code: Mapped[str] = mapped_column(String(128), nullable=False)
    rule_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="firing",
    )  # firing | resolved | error
    window_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    window_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(UTC),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(UTC),
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    resolve_reason: Mapped[str | None] = mapped_column(
        String(32), nullable=True,
    )  # resolved_event | stale_timeout
    total_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    severity: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=2,
    )  # 1严重 | 2中等 | 3轻微（对齐夜莺）
    labels: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    resource_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    details: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)  # 来源明细黑盒
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 事件的数据源归属（取自规则绑定的数据源；直报事件为 NULL，幂等归组时按 0 处理）
    monitoring_source_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 告警类型：log（CH 事件型，恢复=自动关闭语义）| metric（状态型，恢复=状态回归语义）；写入时定型
    rule_kind: Mapped[str | None] = mapped_column(String(8), nullable=True)
    ticket_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # 逻辑引用 tickets.id
    group_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class MonitoringSource(BaseMixin, Base):
    """监控数据源注册表（多套 CH/VM，凭据红线：只存 password_ref 引用名）。

    真凭据留在执行器侧 env，平台不落任何密码（决策 8，同 job-dispatch 只带钥匙名）。
    """

    __tablename__ = "monitoring_sources"

    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # clickhouse | victoria | prometheus
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    database_name: Mapped[str | None] = mapped_column(String(64), nullable=True)  # CH database
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 只读账号
    password_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    secure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # TLS
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class NotifyChannel(BaseMixin, Base):
    """告警通知渠道登记（发送动作仍在执行器，平台只登记配置并随分发体下发）。

    凭据红线：webhook URL 含 secret，只存 secret_ref 引用名，真 URL 在执行器侧 env。
    """

    __tablename__ = "notify_channels"

    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # feishu_webhook（预留 dingtalk | wecom 扩展）
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    secret_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    extra: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)  # @手机号等
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class AlertRule(BaseMixin, Base):
    """告警规则（二期起为分发源：绑定数据源 + 评估契约字段）。

    code 对齐执行器侧 rule_code，是两侧唯一对齐键，变更须人工同步（二期分发后消除）。
    eval_sql 契约：单行两列 error_count + log_details（存量 ck-log-alert SQL 原样可贴）。
    for_rounds：连续 M 轮达标才报 firing（防抖，执行器侧实现，平台无 pending 态）。
    notify_channel_id 可空：空 = 分发体不带渠道，通知由执行器默认处理。
    """

    __tablename__ = "alert_rules"
    __table_args__ = (
        # Index 声明唯一约束（DDL 侧为表级 CONSTRAINT）
        Index("uq_alert_rules_source_code", "source", "code", unique=True),
    )

    source: Mapped[str] = mapped_column(String(32), nullable=False)
    code: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    group_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    stale_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    default_severity: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=2,
    )
    static_labels: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    notify_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # ── 二期：评估契约字段（分发源） ──
    source_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("monitoring_sources.id"), nullable=True,
    )
    eval_sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    threshold: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 规则级扫描间隔（秒）：执行器按它调度本规则；与 interval_minutes（查询窗口）独立
    eval_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    for_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    detail_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    grafana_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    feishu_card_template: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    notify_channel_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("notify_channels.id"), nullable=True,
    )
