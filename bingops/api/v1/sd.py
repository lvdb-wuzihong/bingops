"""Prometheus/vmagent HTTP SD 端点（CMDB 主机服务发现快照）。

契约豁免说明：本端点是机器对机器接口（scraper http_sd_config 消费），
响应体为裸 TargetGroup JSON 数组——不套平台统一 {"code","message","data"}
信封（套了 http_sd 解析直接失败），也不绑定用户权限码（同 VPC 网络隔离
作为访问边界；如需收敛后续可加静态 token 校验）。

全量快照语义：响应始终是当前完整集合，scraper 以响应为准增删目标；
端点 5xx/超时时 scraper 保留上一轮目标继续采集，不会造成监控黑洞。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.api.dependencies import get_db_session
from bingops.services.cmdb import sd_service

router = APIRouter(prefix="/sd/v1", tags=["service-discovery"])


@router.get("/nodes")
async def list_sd_nodes(
    port: int = Query(9100, ge=1, le=65535),
    region: str | None = None,
    vpc: str | None = Query(None, description="VPC provider_id 过滤（如 vpc-bp1xxxx）"),
    model_codes: str | None = Query(
        None, description="逗号分隔的模型 code；缺省 aliyun_ecs,gcp_compute,k8s_node",
    ),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """主机服务发现快照（全量 TargetGroup，供 http_sd_config 轮询）。"""
    codes = (
        [item.strip() for item in model_codes.split(",") if item.strip()]
        if model_codes else None
    )
    return await sd_service.build_host_target_groups(
        session, port=port, region=region, vpc=vpc, model_codes=codes,
    )
