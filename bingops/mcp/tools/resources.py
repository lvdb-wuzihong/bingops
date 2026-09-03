"""MCP 工具：CMDB 资源检索（A 组，设计文档 §4.3-A）。"""

from __future__ import annotations

from bingops.mcp._shared import clamp_limit, mcp_tool_logging, redact, session_scope
from bingops.mcp.server import mcp
from bingops.repositories.cmdb.model_repo import CmdbModelRepo
from bingops.services.cmdb import resource_service

_RESOURCE_FIELDS = (
    "id", "name", "provider", "provider_id", "cloud_account", "region", "zone", "status",
)


@mcp.tool()
@mcp_tool_logging("search_resources")
async def search_resources(
    model_code: str | None = None,
    provider: str | None = None,
    status: str | None = None,
    region: str | None = None,
    cloud_account: str | None = None,
    keyword: str | None = None,
    limit: int | None = None,
) -> dict:
    """按条件检索 CMDB 资源（名称模糊匹配），返回 id/name/model_code/provider/region 等。

    适用场景：按云厂商/地域/状态圈定资源范围、按名称关键词定位实例。
    限制：不支持按 fields 动态字段（如 IP）查询（待平台暴露 JSONB 查询参数）；
    仅返回第 1 页（默认 20 条、上限 100）。
    """
    size = clamp_limit(limit)
    async with session_scope() as session:
        model_repo = CmdbModelRepo(session)
        model_id = None
        if model_code:
            model = await model_repo.get_model_by_code(model_code)
            if model is None:
                from bingops.core.exceptions import NotFoundError

                raise NotFoundError("CmdbModel", model_code)
            model_id = model.id

        rows, total = await resource_service.list_resources(
            session,
            model_id=model_id, provider=provider, status=status,
            cloud_account=cloud_account, region=region, keyword=keyword,
            page=1, page_size=size,
        )
        code_map = {
            m.id: m.code
            for m in await model_repo.get_models_by_ids([r.model_id for r in rows])
        } if rows else {}

    return {
        "items": [
            {**{k: getattr(r, k) for k in _RESOURCE_FIELDS}, "model_code": code_map.get(r.model_id)}
            for r in rows
        ],
        "total": total,
    }


@mcp.tool()
@mcp_tool_logging("get_resource_detail")
async def get_resource_detail(resource_id: int) -> dict:
    """获取单个资源详情：通用字段 + 动态 fields（已脱敏）+ 所属模型 code。

    适用场景：变更风险预检确认目标资源规格、根因分析读取实例属性（IP/规格/版本等）。
    限制：fields 中命中敏感 key（password/token/secret 等）的值已替换为 ***；
    资源不存在时返回 not_found。
    """
    async with session_scope() as session:
        resource = await resource_service.get_resource(session, resource_id)
        model = await CmdbModelRepo(session).get_model(resource.model_id)

    synced_at = resource.synced_at.isoformat() if resource.synced_at else None
    return {
        "id": resource.id,
        "name": resource.name,
        "model_code": model.code if model else None,
        "provider": resource.provider,
        "provider_id": resource.provider_id,
        "cloud_account": resource.cloud_account,
        "region": resource.region,
        "zone": resource.zone,
        "status": resource.status,
        "fields": redact(resource.fields or {}),
        "synced_at": synced_at,
    }
