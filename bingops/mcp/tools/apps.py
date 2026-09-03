"""MCP 工具：业务应用目录（A 组，设计文档 §4.3-A）。"""

from __future__ import annotations

from bingops.mcp._shared import clamp_limit, mcp_tool_logging, session_scope
from bingops.mcp.server import mcp
from bingops.services.cmdb import business_app_service as app_service

_APP_FIELDS = ("id", "name", "app_code", "team", "owner", "department", "repo_url")


@mcp.tool()
@mcp_tool_logging("list_business_apps")
async def list_business_apps(
    team: str | None = None,
    owner: str | None = None,
    keyword: str | None = None,
    limit: int | None = None,
) -> dict:
    """列出业务应用（id/name/负责人/仓库），支持按团队、负责人、关键词过滤。

    适用场景：巡检日报拉取巡检对象清单（按团队分组）、成本分析按应用维度枚举分摊对象。
    限制：仅返回第 1 页（默认 20 条、上限 100），不支持跨页；对象多时用 team/keyword 收窄范围。
    """
    size = clamp_limit(limit)
    async with session_scope() as session:
        rows, total = await app_service.list_apps(
            session, team=team, owner=owner, keyword=keyword, page=1, page_size=size,
        )
    items = [
        {k: getattr(a, k) for k in _APP_FIELDS}
        for a in rows
    ]
    return {"items": items, "total": total}


@mcp.tool()
@mcp_tool_logging("get_app_overview")
async def get_app_overview(app_id: int) -> dict:
    """获取单个业务应用全貌：基础信息 + repo_url/pipelines + 挂载资源清单（含 env/region）。

    适用场景：告警根因分析确定应用边界与资源范围、巡检日报展开单应用检查项。
    限制：应用不存在时返回 not_found；资源清单不含 Pod/Node 等基础设施层 CI（应用只绑服务级 CI）。
    """
    async with session_scope() as session:
        app = await app_service.get_app(session, app_id)
        resources = await app_service.list_app_resources(session, app_id)
    return {
        "id": app.id,
        "name": app.name,
        "app_code": app.app_code,
        "team": app.team,
        "owner": app.owner,
        "department": app.department,
        "repo_url": app.repo_url,
        "pipelines": app.pipelines,
        "resources": resources,
    }


@mcp.tool()
@mcp_tool_logging("find_app_by_resource")
async def find_app_by_resource(resource_id: int) -> dict:
    """由 CMDB 资源 ID 反查归属的业务应用（逆向定位）。

    适用场景：告警/指标事件携带实例或资源 ID 时，反查应用与负责人做上下文富化（根因分析入口）。
    限制：资源未挂接到任何应用时返回空 apps 列表（不代表资源不存在）。
    """
    async with session_scope() as session:
        rows = await app_service.list_resource_apps(session, resource_id)
    return {
        "resource_id": resource_id,
        "apps": [
            {"id": r["app_id"], "name": r["name"], "app_code": r["app_code"], "source": r["source"]}
            for r in rows
        ],
    }
