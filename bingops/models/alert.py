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
            "source", "rule_code",
            unique=True,
            postgresql_where=text("status = 'firing'"),
        ),
        Index("idx_alert_events_status_time", "status", "first_seen_at"),
        Index(
            "idx_alert_events_last_seen",
            "last_seen_at",
            postgresql_where=text("status = 'firing'"),
        ),
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
    ticket_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # 逻辑引用 tickets.id
    group_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class AlertRule(BaseMixin, Base):
    """规则映射元数据（一期平台侧配置源，非分发源）。

    code 对齐执行器侧 rule_code，是两侧唯一对齐键，变更须人工同步（二期分发后消除）。
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
