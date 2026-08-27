"""工单系统 ORM 模型。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bingops.models.base import Base, BaseMixin
from bingops.models.user import User


class Ticket(BaseMixin, Base):
    """工单主表。

    面向运维场景的工单：申请（request）、变更（change）、故障上报（incident）、通用（general）。

    状态流转：
        open → in_progress → resolved → closed
        open / in_progress → cancelled
        resolved → in_progress（重开）
    """

    __tablename__ = "tickets"

    ticket_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    ticket_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="general",
    )  # general|request|change|incident
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open",
    )  # open|in_progress|resolved|closed|cancelled
    priority: Mapped[str] = mapped_column(
        String(16), nullable=False, default="medium",
    )  # low|medium|high|urgent
    creator_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False,
    )
    assignee_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True,
    )
    related_resource_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("cmdb_resources.id", ondelete="SET NULL"), nullable=True,
    )
    # 变更工单携带的执行意图（P3 审批挂接）
    runbook_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("runbooks.id"), nullable=True,
    )
    job_params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    code_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approval_status: Mapped[str | None] = mapped_column(
        String(16), nullable=True,
    )  # none|pending|approved|rejected
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    creator: Mapped[User] = relationship(foreign_keys=[creator_id])
    assignee: Mapped[User | None] = relationship(foreign_keys=[assignee_id])


class TicketComment(Base):
    """工单流转/评论记录表（不可变，仅 created_at，不使用 BaseMixin）。"""

    __tablename__ = "ticket_comments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    # create|comment|assign|status_change
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_value: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_value: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    user: Mapped[User] = relationship()


class TicketApproval(Base):
    """工单审批记录表（不可变，仅 created_at）。"""

    __tablename__ = "ticket_approvals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False,
    )
    approver_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)  # approve|reject
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    approver: Mapped[User] = relationship()


class ChangeFreeze(BaseMixin, Base):
    """变更封禁窗口（P3 风控栅栏）。

    scope=None 表示全局封禁；scope 为 JSONB 数组时仅限定模型范围，
    如 ["aliyun_ecs", "gcp_compute"]。
    """

    __tablename__ = "change_freezes"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
