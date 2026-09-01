"""CMDB Kafka 消息 Pydantic 模型。

定义 K8s 事件和云资源同步消息的标准格式。
K8s 消息契约以 cmdb-informer（Go）的 MQMessage 为事实源。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


# ── K8s 事件消息 ────────────────────────────────────────────────────────────────


class K8sSyncType(str, Enum):
    """同步类型（对齐 Go message.SyncType）。"""

    FULL_SYNC = "full_sync"
    PERIODIC_SYNC = "periodic_sync"
    WATCH_EVENT = "watch_event"


class K8sEventType(str, Enum):
    """事件类型（对齐 Go message.EventType）。"""

    ADD = "add"
    UPDATE = "update"
    DELETE = "delete"
    SNAPSHOT = "snapshot"


class K8sResourceData(BaseModel):
    """K8s 资源数据（对齐 Go message.ResourceData）。

    全量快照消息仅携带 raw；Watch 增量可能额外携带 spec/status。
    """

    uid: str = Field(default="", description="K8s UID")
    name: str = Field(description="资源名称")
    namespace: str = Field(default="", description="命名空间，集群级资源为空")
    labels: dict[str, str] = Field(default_factory=dict, description="K8s labels")
    annotations: dict[str, str] = Field(default_factory=dict, description="K8s annotations")
    resource_version: str = Field(default="", description="K8s resourceVersion，用于幂等校验")
    status: dict | None = Field(default=None, description="资源 status 段")
    spec: dict | None = Field(default=None, description="资源 spec 段")
    raw: dict | None = Field(default=None, description="完整原始对象 JSON")


class K8sResourceMessage(BaseModel):
    """K8s 资源变更消息（Topic: k8s-events-{cluster_id}，对齐 Go MQMessage）。

    由 Informer 产生，通过 Kafka 传递给 CMDB 消费者。
    """

    message_id: str = Field(description="消息唯一 ID")
    cluster_id: str = Field(description="集群标识，如 ack-cn-shanghai")
    cluster_type: str = Field(default="", description="集群类型，如 ack | gke")
    sync_type: K8sSyncType = Field(description="同步类型")
    event_type: K8sEventType = Field(description="事件类型")
    resource_type: str = Field(description="资源类型: pods | deployments | nodes | ...")
    timestamp: datetime = Field(description="事件时间")
    resource: K8sResourceData = Field(description="资源数据")
    old_resource: K8sResourceData | None = Field(default=None, description="仅 update 时有值")


# ── 云资源同步消息 ──────────────────────────────────────────────────────────────


class CloudSyncEventType(str, Enum):
    """云资源同步事件类型。"""

    UPSERT = "upsert"
    DELETE = "delete"


class CloudResourceMessage(BaseModel):
    """云资源同步消息（Topic: cloud-sync-{provider}）。

    由定时采集器产生，通过 Kafka 传递给 CMDB 消费者。
    """

    provider: str = Field(description="云厂商标识: aliyun | gcp")
    resource_type: str = Field(description="资源类型: host | database | vpc | ...")
    provider_id: str = Field(description="云厂商原始资源 ID")
    cloud_account: str = Field(description="云账号标识")
    event_type: CloudSyncEventType = Field(description="同步事件类型")
    resource_version: str = Field(default="1", description="同步版本号，用于幂等")

    # 资源基础信息
    name: str = Field(default="", description="资源名称")
    region: str | None = Field(default=None, description="地域")
    zone: str | None = Field(default=None, description="可用区")
    status: str | None = Field(
        default=None,
        description="资源状态（running/stopped/maintenance/unknown）；None = 该资源类型无生命周期状态，不适用",
    )
    attributes: dict = Field(default_factory=dict, description="扩展属性")

    # 云标签（由采集器从云 API 同步）
    cloud_tags: dict[str, str] = Field(default_factory=dict, description="云厂商原始标签")

    # 关系提示（采集器根据资源元数据推断）
    parent_provider_id: str | None = Field(default=None, description="父资源 provider_id（用于重建从属关系）")
    parent_resource_type: str | None = Field(default=None, description="父资源类型")

    timestamp: datetime = Field(default_factory=lambda: datetime.now(), description="同步时间")
