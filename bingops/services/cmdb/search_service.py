"""CMDB 全局搜索业务服务（跨 应用/资源 聚合，工作台搜索页）。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from bingops.models.cmdb.resource import CmdbResource
from bingops.repositories.cmdb.business_app_repo import CmdbBusinessAppRepo
from bingops.repositories.cmdb.model_repo import CmdbModelRepo
from bingops.services.cmdb import resource_service

# 各分组返回上限（搜索页只展示头部命中，更多走对应域的列表页）
_PER_GROUP_LIMIT = 20


async def search_all(
    session: AsyncSession, q: str, *, exact: bool = False, limit: int = _PER_GROUP_LIMIT,
) -> dict:
    """全局搜索：一次请求分组返回应用与资源命中。

    - 应用：name / app_code 匹配（exact 时等值）
    - 资源：name / provider_id 匹配（exact 时等值）**或**动态字段值精确
      （field_value 恒参与，IP/连接地址等场景用）——两路命中合并去重
    """
    q = (q or "").strip()
    if not q:
        return {"apps": [], "resources": []}

    # ── 应用 ──
    app_repo = CmdbBusinessAppRepo(session)
    apps, _ = await app_repo.list_apps(keyword=q, page=1, page_size=limit)
    if exact:
        apps = [
            a for a in apps if a.name == q or a.app_code == q
        ]

    # ── 资源：keyword 路 ∪ 动态字段值路（IP 等场景 keyword 不含 IP）──
    kw_resources, _ = await resource_service.list_resources(
        session, keyword=q, exact=exact, page=1, page_size=limit,
    )
    fv_resources, _ = await resource_service.list_resources(
        session, field_value=q, page=1, page_size=limit,
    )
    merged: dict[int, CmdbResource] = {r.id: r for r in fv_resources}
    for r in kw_resources:
        merged.setdefault(r.id, r)
    resources = list(merged.values())[:limit]
    model_repo = CmdbModelRepo(session)
    code_map = {
        m.id: m.code
        for m in await model_repo.get_models_by_ids([r.model_id for r in resources])
    } if resources else {}

    return {
        "apps": [
            {
                "id": a.id,
                "app_code": a.app_code,
                "name": a.name,
                "owner": a.owner,
                "team": a.team,
            }
            for a in apps
        ],
        "resources": [
            {
                "id": r.id,
                "name": r.name,
                "model_id": r.model_id,
                "model_code": code_map.get(r.model_id),
                "provider": r.provider,
                "region": r.region,
                "status": r.status,
            }
            for r in resources
        ],
    }
