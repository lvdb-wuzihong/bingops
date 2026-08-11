"""CMDB 同步任务 Pydantic Schemas。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SyncTaskCreate(BaseModel):
    """创建同步任务请求。"""

    name: str = Field(min_length=1, max_length=256, description="任务名称")
    task_type: str = Field(description="任务类型: k8s | cloud")
    provider: str | None = Field(default=None, max_length=32, description="云厂商: aliyun|aws|gcp（cloud 必填）")
    target_id: str = Field(min_length=1, max_length=256, description="目标标识: 集群ID 或 云账号ID")
    resource_types: list[str] = Field(default_factory=list, description="同步的资源类型列表")
    schedule: str | None = Field(default=None, max_length=64, description="cron 表达式（cloud 类型使用）")
    enabled: bool = Field(default=True, description="是否启用")
    description: str | None = Field(default=None, description="描述")


class SyncTaskUpdate(BaseModel):
    """更新同步任务请求（所有字段可选）。"""

    name: str | None = Field(default=None, max_length=256)
    provider: str | None = Field(default=None, max_length=32)
    resource_types: list[str] | None = None
    schedule: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None
    description: str | None = None


class SyncTaskResponse(BaseModel):
    """同步任务响应。"""

    id: int
    name: str
    task_type: str
    provider: str | None = None
    target_id: str
    resource_types: list[str] = Field(default_factory=list)
    schedule: str | None = None
    enabled: bool
    description: str | None = None
    last_synced_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
