"""CMDB 业务应用 / 业务域 Pydantic 模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


def _validate_pipelines(value: dict | None) -> dict:
    """流水线地址 map 校验：{环境: 地址}，值为非空字符串。"""
    if value is None:
        return {}
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise ValueError("pipelines must be a mapping of env -> url strings")
    return value


def _validate_dependencies(value: list | None) -> list:
    """依赖声明契约校验：internal 必须带 app_code；external 必须带 name/url 之一。

    条目可选 env 字段（环境名，如 prod）：空 = 全环境通用；声明后应用拓扑
    按 ?env= 过滤依赖边（如内部业务 test 连 nacos-test、prod 连
    nacos-internal-prod 的分环境声明）。
    """
    if value is None:
        return []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("dependencies items must be objects")
        dep_type = item.get("type")
        if dep_type == "internal":
            if not item.get("app_code"):
                raise ValueError("internal dependency requires app_code")
        elif dep_type == "external":
            if not (item.get("url") or item.get("name")):
                raise ValueError("external dependency requires url or name")
        else:
            raise ValueError(f"unsupported dependency type: {dep_type!r}")
    return value


class BusinessAppCreate(BaseModel):
    """创建业务应用请求。"""

    app_code: str = Field(min_length=1, max_length=64, description="应用编码，全局唯一")
    name: str = Field(min_length=1, max_length=256, description="应用名称")
    description: str | None = Field(default=None, description="应用描述")
    team: str | None = Field(default=None, max_length=128, description="所属团队")
    owner: str | None = Field(default=None, max_length=128, description="负责人")
    department: str | None = Field(default=None, max_length=128, description="所属部门")
    labels: dict = Field(default_factory=dict, description="应用级标签")
    repo_url: str | None = Field(default=None, max_length=512, description="代码仓库地址")
    pipelines: dict = Field(
        default_factory=dict,
        description="各环境流水线地址，{环境: 地址}，key 对齐 env 标签值域",
    )
    business_id: int | None = Field(default=None, description="归属业务域 ID")
    dependencies: list = Field(
        default_factory=list,
        description="依赖声明：[{type: internal, app_code} / {type: external, name, url}]",
    )

    _check_pipelines = field_validator("pipelines")(_validate_pipelines)
    _check_dependencies = field_validator("dependencies")(_validate_dependencies)


class BusinessAppUpdate(BaseModel):
    """更新业务应用请求。"""

    name: str | None = Field(default=None, max_length=256)
    description: str | None = None
    team: str | None = Field(default=None, max_length=128)
    owner: str | None = Field(default=None, max_length=128)
    department: str | None = Field(default=None, max_length=128)
    labels: dict | None = None
    repo_url: str | None = Field(default=None, max_length=512)
    pipelines: dict | None = None
    business_id: int | None = None
    dependencies: list | None = None

    _check_pipelines = field_validator("pipelines")(_validate_pipelines)
    _check_dependencies = field_validator("dependencies")(_validate_dependencies)


class BusinessAppResponse(BaseModel):
    """业务应用响应。"""

    id: int
    app_code: str
    name: str
    description: str | None = None
    team: str | None = None
    owner: str | None = None
    department: str | None = None
    labels: dict = Field(default_factory=dict)
    repo_url: str | None = None
    pipelines: dict = Field(default_factory=dict)
    business_id: int | None = None
    dependencies: list = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class BusinessDomainCreate(BaseModel):
    """创建业务域请求。"""

    name: str = Field(min_length=1, max_length=128, description="业务域名称")
    code: str = Field(min_length=1, max_length=64, description="业务域编码，全局唯一")
    owner: str | None = Field(default=None, max_length=128, description="业务负责人")
    description: str | None = None


class BusinessDomainUpdate(BaseModel):
    """更新业务域请求。"""

    name: str | None = Field(default=None, max_length=128)
    owner: str | None = Field(default=None, max_length=128)
    description: str | None = None


class BusinessDomainResponse(BaseModel):
    """业务域响应（含归属应用数）。"""

    id: int
    code: str
    name: str
    owner: str | None = None
    description: str | None = None
    app_count: int = Field(default=0, description="归属应用数量")
    created_at: datetime
    updated_at: datetime
