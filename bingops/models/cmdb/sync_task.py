"""CMDB 同步任务配置 ORM 模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bingops.models.base import Base, BaseMixin


class CmdbSyncTask(BaseMixin, Base):
    """CMDB 同步任务配置表。

    管理 K8s / 云 API 两类同步任务的开关与调度策略。
    - K8s 任务：仅需 enabled 开关（Informer 事件驱动）
    - Cloud 任务：enabled + schedule（cron 定时轮询）
    - 同一 (task_type, target_id) 允许多个任务：按资源类型拆分独立调度
    """

    __tablename__ = "cmdb_sync_tasks"

    name: Mapped[str] = mapped_column(String(256), nullable=False)
    task_type: Mapped[str] = mapped_column(String(16), nullable=False)  # 'k8s' | 'cloud'
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)  # aliyun|aws|gcp
    target_id: Mapped[str] = mapped_column(String(256), nullable=False)  # 集群ID / 云账号ID
    resource_types: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    schedule: Mapped[str | None] = mapped_column(String(64), nullable=True)  # cron 表达式
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
