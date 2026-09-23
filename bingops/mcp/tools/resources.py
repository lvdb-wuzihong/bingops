"""MCP 工具：CMDB 资源检索（A 组，设计文档 §4.3-A）。"""

from __future__ import annotations

from bingops.mcp._shared import clamp_limit, mcp_tool_logging, redact, session_scope
from bingops.mcp.server import mcp
from bingops.repositories.cmdb.model_repo import CmdbModelRepo
from bingops.services.cmdb import model_service, resource_service, search_service

_RESOURCE_FIELDS = (
    "id", "name", "provider", "provider_id", "cloud_account", "region", "zone", "status",
)


def _matched_keys(fields: dict, value: str) -> list[str]:
    """递归收集 fields 中值等于 value 的顶层键（嵌套数组/对象内命中归属顶层键）。"""

    def contains(node) -> bool:
        if isinstance(node, str):
            return node == value
        if isinstance(node, dict):
            return any(contains(v) for v in node.values())
        if isinstance(node, list):
            return any(contains(v) for v in node)
        return False

    return sorted(k for k, v in (fields or {}).items() if contains(v))


@mcp.tool()
@mcp_tool_logging("search_resources")
async def search_resources(
    model_code: str | None = None,
    provider: str | None = None,
    status: str | None = None,
    region: str | None = None,
    cloud_account: str | None = None,
    keyword: str | None = None,
    field_value: str | None = None,
    limit: int | None = None,
) -> dict:
    """按条件检索 CMDB 资源，返回 id/name/model_code/provider/region 等。

    适用场景：拿到 IP/连接地址/实例 ID 反查资源（告警排障、日志定位的
    第一步）；也可按云厂商/地域/状态圈定范围或按名称关键词模糊定位。
    field_value 对资源的全部动态字段做精确等值匹配（IP、endpoint、
    instance_id 等，含嵌套数组），例：field_value="10.0.0.5"、
    field_value="rm-xxx.mysql.rds.aliyuncs.com"。
    限制：field_value 是全字段精确等值（非模糊，不支持按端口等数字字段）；
    仅返回第 1 页（默认 20 条、上限 100）；命中后可用 get_resource_detail
    看完整属性、find_app_by_resource 反查归属应用。
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
            field_value=field_value,
            page=1, page_size=size,
        )
        code_map = {
            m.id: m.code
            for m in await model_repo.get_models_by_ids([r.model_id for r in rows])
        } if rows else {}

    return {
        "items": [
            {
                **{k: getattr(r, k) for k in _RESOURCE_FIELDS},
                "model_code": code_map.get(r.model_id),
                # 仅 field_value 检索时附带：告知 agent 命中了哪个动态字段
                **({
                    "matched_fields": _matched_keys(r.fields or {}, field_value),
                } if field_value else {}),
            }
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


@mcp.tool()
@mcp_tool_logging("search_assets")
async def search_assets(
    q: str,
    exact: bool = False,
    limit: int | None = None,
) -> dict:
    """跨域全局搜索：一次拿到业务应用与 CMDB 资源的命中（工作台搜索同源）。

    适用场景：不确定目标在应用还是资源时优先用本工具（告警/日志给的
    IP、主机名、域名先全局搜一遍）；确定只搜资源时用 search_resources
    能带 matched_fields，确定只搜应用时用 list_business_apps。
    匹配规则：应用 name/app_code；资源 name/provider_id 模糊，且动态
    字段值（IP/连接地址/实例 ID）恒为精确等值。exact=true 时全部转等值。
    限制：每分组默认 20 条、上限 50；更多命中用对应域列表工具缩小范围。
    """
    size = clamp_limit(limit, default=20, maximum=50)
    async with session_scope() as session:
        return await search_service.search_all(session, q, exact=exact, limit=size)


@mcp.tool()
@mcp_tool_logging("get_models_overview")
async def get_models_overview() -> dict:
    """获取 CMDB 全部模型分类与资源计数（平台结构总览）。

    适用场景：回答平台有哪些资源类型/各多少量、巡检报告开头描述资产
    规模、判断某类资源是否已纳入 CMDB 管理。
    限制：只含分类/模型/存活资源计数，不含资源明细；查具体资源用
    search_assets 或 search_resources。
    """
    async with session_scope() as session:
        return {"categories": await model_service.get_models_overview(session)}
