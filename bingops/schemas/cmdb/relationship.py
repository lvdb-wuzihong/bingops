"""CMDB 关系 Pydantic 模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ── 从属关系 (Belongs To) ─────────────────────────────────────────────────────


class BelongsToCreate(BaseModel):
    """创建从属关系请求。"""

    child_id: int = Field(gt=0, description="子资源 ID")
    parent_id: int = Field(gt=0, description="父资源 ID")
    description: str | None = Field(default=None, max_length=256, description="关系语义描述，如 集群归属 | 调度于")


class BelongsToResponse(BaseModel):
    """从属关系响应。"""

    id: int
    child_id: int
    parent_id: int
    description: str | None = None
    synced_at: datetime | None = None
    source: str
    created_at: datetime


# ── 关联关系 (Relates To) ─────────────────────────────────────────────────────


class RelatesToCreate(BaseModel):
    """创建关联关系请求。"""

    source_id: int = Field(gt=0, description="源资源 ID")
    target_id: int = Field(gt=0, description="目标资源 ID")
    description: str | None = Field(default=None, max_length=256, description="关系语义描述，如 selector 匹配 | 承载于")
    attributes: dict = Field(default_factory=dict, description="关系附加属性")


class RelatesToResponse(BaseModel):
    """关联关系响应。"""

    id: int
    source_id: int
    target_id: int
    description: str | None = None
    attributes: dict = Field(default_factory=dict)
    synced_at: datetime | None = None
    source: str
    created_at: datetime


# ── 拓扑子图 ─────────────────────────────────────────────────────────────────


class TopologyNode(BaseModel):
    """拓扑图节点（瘦身负载，不含 fields）。"""

    id: int
    name: str
    model_id: int
    model_code: str | None = None
    model_name: str | None = None
    provider: str | None = None
    status: str
    region: str | None = None
    is_center: bool = False


class TopologyEdge(BaseModel):
    """拓扑图边（保留方向：belongs_to child→parent；relates_to source→target）。"""

    id: int
    relation_type: str = Field(description="belongs_to | relates_to")
    source_id: int
    target_id: int
    description: str | None = None
    kind: str | None = None
    source: str | None = None


class TopologyResponse(BaseModel):
    """拓扑子图响应。"""

    center_id: int
    depth: int
    truncated: bool = Field(default=False, description="高扇出截断标记")
    nodes: list[TopologyNode]
    edges: list[TopologyEdge]
