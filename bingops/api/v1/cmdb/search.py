"""CMDB 全局搜索 API（工作台搜索页，跨 应用/资源 聚合）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.api.dependencies import get_db_session, require_permission
from bingops.core.response import success_response
from bingops.models.user import User
from bingops.services.cmdb import search_service

router = APIRouter(prefix="/api/v1/cmdb/search", tags=["cmdb-search"])


@router.get("")
async def search(
    q: str = Query(min_length=1, description="搜索关键词（IP/主机名/应用/负责人等）"),
    exact: bool = Query(default=False, description="精确匹配开关（默认模糊）"),
    limit: int = Query(default=20, ge=1, le=50, description="每分组返回上限"),
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("cmdb_resource:list"),
):
    """全局搜索：分组返回应用与资源命中。

    - 应用：name / app_code 匹配
    - 资源：name / provider_id 匹配；exact=true 时为等值，且动态字段
      （IP/连接地址/实例 ID 等）恒为精确等值匹配
    - 各分组默认返回 20 条，更多走对应域的列表页
    """
    data = await search_service.search_all(session, q, exact=exact, limit=limit)
    return success_response(data=data)
