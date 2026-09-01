"""Prometheus/vmagent HTTP SD 服务层（主机采集目标快照装配）。

契约豁免说明（语义分化）：消费者是 scraper 的 http_sd_config 解析器（机器对机器），
响应体必须是裸 TargetGroup JSON 数组；包装平台统一 {"code","message","data"} 信封会
导致解析失败。全量快照语义：每次轮询返回完整集合，响应中未出现的目标会被 scraper 移除。

labels 口径：
- provider/region/zone 取通用列，hostname 取资源 name
- vpc 沿 belongs_to 边向上寻祖至 vpc 模型实例，取 provider_id（稳定唯一键，
  不用 name——改名会造成 Prometheus 时间序列跳变）
- env/app 走标签体系，manual 优先于 cloud（与环境维度决策同源）；缺值时该
  label 整体省略，scraper 侧可用 absent() 感知漏标，不伪造空值
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from bingops.models.cmdb.resource import CmdbResource
from bingops.repositories.cmdb.relationship_repo import CmdbRelationshipRepo
from bingops.repositories.cmdb.resource_repo import CmdbResourceRepo
from bingops.repositories.cmdb.tag_repo import CmdbTagRepo

logger = logging.getLogger(f"bingops.{__name__}")

# 默认主机模型范围（与任务执行面同源 + K8s 节点）
DEFAULT_MODEL_CODES: list[str] = ["aliyun_ecs", "gcp_compute", "k8s_node"]

# 与 job_service._IP_FIELD_CANDIDATES 同口径（内网地址，同 VPC 可达）
_IP_FIELD_CANDIDATES = ("private_ip", "internal_ip", "ip")

# 运行态集合：实际值是云同步链路写入的原始云状态（running/ready），
# 与 CMDB 生命周期 online/offline 语义的偏差另行收敛；stopped 明确不采
_SD_STATUSES = ("running", "ready")

# 参与 labels 的业务标签及来源优先级（越小越优先）
_SD_TAG_KEYS = ("env", "app")
_TAG_SOURCE_PRIORITY = ("manual", "cloud")

# 向上寻祖 vpc 的深度上限（ECS 两跳，k8s_node 可能跨三跳以上）
_MAX_ANCESTOR_DEPTH = 5

# vpc 类模型 code（实际库为厂商前缀命名：aliyun_vpc 等，预设 vpc 一并列出兼容）
_VPC_MODEL_CODES = ("vpc", "aliyun_vpc", "aws_vpc", "gcp_vpc")


def _extract_ip(fields: dict) -> str | None:
    """按候选键顺序提取主机内网 IP。"""
    for key in _IP_FIELD_CANDIDATES:
        value = fields.get(key)
        if value:
            return str(value)
    return None


def _resolve_vpc(
    resource_id: int, edges: dict[int, list[int]], vpc_by_id: dict[int, str],
) -> str | None:
    """沿 belongs_to 边向上寻祖，命中 vpc 实例即返回其 provider_id。"""
    visited: set[int] = {resource_id}
    frontier = [resource_id]
    for _ in range(_MAX_ANCESTOR_DEPTH):
        nxt: list[int] = []
        for node in frontier:
            for parent in edges.get(node, ()):
                if parent in visited:
                    continue
                provider_id = vpc_by_id.get(parent)
                if provider_id:
                    return provider_id
                visited.add(parent)
                nxt.append(parent)
        if not nxt:
            return None
        frontier = nxt
    return None


def _pick_tag_value(candidates: list[tuple[int, str]]) -> str | None:
    """按来源优先级取标签值（同优先级取字典序第一个，保证稳定）。"""
    return min(candidates)[1] if candidates else None


async def build_host_target_groups(
    session: AsyncSession,
    *,
    port: int,
    region: str | None = None,
    vpc: str | None = None,
    model_codes: list[str] | None = None,
) -> list[dict]:
    """装配主机 SD 目标组快照（一机一组）。

    Args:
        session: 数据库会话。
        port: 采集端口，拼进 targets 的 host:port。
        region: 地域过滤（通用列精确匹配）。
        vpc: VPC 过滤（provider_id，沿 belongs_to 寻祖匹配）。
        model_codes: 模型范围，缺省 DEFAULT_MODEL_CODES。

    Returns:
        TargetGroup 列表：[{"targets": ["ip:port"], "labels": {...}}]，
        按 id 升序稳定输出；同 IP 多模型重复主机只保留首个。
    """
    codes = model_codes or DEFAULT_MODEL_CODES
    resource_repo = CmdbResourceRepo(session)
    hosts = await resource_repo.list_sd_hosts(
        model_codes=codes, statuses=list(_SD_STATUSES), region=region,
    )
    if not hosts:
        return []

    resource_ids = [res.id for res, _ in hosts]
    tag_rows = await CmdbTagRepo(session).list_by_resource_ids_and_keys(
        resource_ids, list(_SD_TAG_KEYS),
    )
    edge_rows = await CmdbRelationshipRepo(session).list_belongs_to_edges()
    vpc_rows = await resource_repo.list_alive_by_model_codes(list(_VPC_MODEL_CODES))

    edges: dict[int, list[int]] = {}
    for child, parent in edge_rows:
        edges.setdefault(child, []).append(parent)
    vpc_by_id = {
        row.id: row.provider_id for row in vpc_rows if row.provider_id
    }

    # {resource_id: {tag_key: [(来源优先级, 值)]}}
    tags_by_resource: dict[int, dict[str, list[tuple[int, str]]]] = {}
    for tag in tag_rows:
        priority = (
            _TAG_SOURCE_PRIORITY.index(tag.source)
            if tag.source in _TAG_SOURCE_PRIORITY else len(_TAG_SOURCE_PRIORITY)
        )
        tags_by_resource.setdefault(tag.resource_id, {}).setdefault(
            tag.tag_key, [],
        ).append((priority, tag.tag_value))

    groups: list[dict] = []
    seen_ips: set[str] = set()
    for res, _model_code in hosts:
        ip = _extract_ip(res.fields or {})
        if ip is None or ip in seen_ips:
            continue
        seen_ips.add(ip)

        resolved_vpc = _resolve_vpc(res.id, edges, vpc_by_id)
        if vpc is not None and resolved_vpc != vpc:
            continue

        res_tags = tags_by_resource.get(res.id, {})
        labels: dict[str, str] = {}
        if res.provider:
            labels["provider"] = res.provider
        for key in _SD_TAG_KEYS:
            value = _pick_tag_value(res_tags.get(key, []))
            if value:
                labels[key] = value
        if res.region:
            labels["region"] = res.region
        if res.zone:
            labels["zone"] = res.zone
        if resolved_vpc:
            labels["vpc"] = resolved_vpc
        labels["hostname"] = res.name

        groups.append({"targets": [f"{ip}:{port}"], "labels": labels})

    logger.debug(
        "SD host snapshot built",
        extra={
            "groups": len(groups),
            "hosts": len(hosts),
            "region": region,
            "vpc_filter": vpc,
            "model_codes": codes,
        },
    )
    return groups
