"""中转网关业务层：选路与可达性。

网关描述的是"这台机器要怎么才被连到"，属于网络拓扑事实；因此选路必须在
执行期由机器归属算出来，而不是让每个 runbook 声明一次（漏声明的后果是 SSH
超时，现象像 playbook 写错，排查成本极高）。设计见 §5.2。
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.core.exceptions import ConflictError, NotFoundError, ValidationError
from bingops.models.cmdb.model import CmdbModel
from bingops.models.cmdb.resource import CmdbResource
from bingops.models.job_gateway import GATEWAY_SCOPE_KEYS, JobGateway
from bingops.models.user import User
from bingops.repositories.job_gateway_repo import JobGatewayRepo
from bingops.schemas.job_gateway import GatewayCreate, GatewayUpdate
from bingops.services import credential_service

logger = logging.getLogger(f"bingops.{__name__}")

# 可达性视图默认覆盖的机型（只有这些模型才是可 SSH 的执行目标）
REACHABILITY_MODEL_CODES = ("aliyun_ecs", "gcp_compute")

# scope 维度 → 主机字段映射（resource_ids 单独按主键判）
_SCOPE_FIELD_MAP = (
    ("cloud_accounts", "cloud_account"),
    ("regions", "region"),
    ("vpc_ids", "vpc_id"),
)


def scope_matches(scope: dict, host: dict) -> bool:
    """scope 任一维度命中即视为可服务这台机器。

    空 scope **不匹配任何机器**：不做"全局兜底网关"，否则一个误配的条目
    会把全部流量接管过去，那种故障比连不上更难查。
    """
    if not scope:
        return False
    if host.get("resource_id") in (scope.get("resource_ids") or []):
        return True
    for scope_key, host_field in _SCOPE_FIELD_MAP:
        value = host.get(host_field)
        if value and value in (scope.get(scope_key) or []):
            return True
    return False


def pick_gateway(gateways: list[JobGateway], host: dict) -> JobGateway | None:
    """取命中且 priority 最小的网关（同 priority 按 name 稳定排序）。

    排序在函数内做，不要求调用方传已排序的列表——“忘了排序就选到另一个跳板”
    这种隐性前提不该存在，而候选集本来就小，排一下没有代价。
    """
    for gateway in sorted(gateways, key=lambda g: (g.priority, g.name)):
        if scope_matches(gateway.scope or {}, host):
            return gateway
    return None


def _validate_scope(scope: dict) -> None:
    unknown = sorted(set(scope) - set(GATEWAY_SCOPE_KEYS))
    if unknown:
        raise ValidationError(
            f"unknown scope keys {unknown}; supported: {list(GATEWAY_SCOPE_KEYS)}"
        )
    if not any(scope.get(k) for k in GATEWAY_SCOPE_KEYS):
        raise ValidationError(
            "scope is empty: 该网关将匹配不到任何机器（不提供全局兜底语义）。"
            f"至少填一个维度：{list(GATEWAY_SCOPE_KEYS)}"
        )


async def list_gateways(
    session: AsyncSession,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[JobGateway], int]:
    return await JobGatewayRepo(session).list(keyword, page, page_size)


async def get_gateway(session: AsyncSession, gateway_id: int) -> JobGateway:
    gateway = await JobGatewayRepo(session).get_by_id(gateway_id)
    if gateway is None:
        raise NotFoundError("JobGateway", str(gateway_id))
    return gateway


async def create_gateway(
    session: AsyncSession, payload: GatewayCreate, user: User,
) -> JobGateway:
    repo = JobGatewayRepo(session)
    if await repo.get_by_name(payload.name):
        raise ConflictError("JobGateway", f"name '{payload.name}' already exists")
    scope = payload.scope.model_dump()
    _validate_scope(scope)
    if payload.ssh_credential:
        # 引用必须存在于凭据目录且是 ssh_key：把"配了但取不到钥匙"留在写入口
        await credential_service.resolve_credential_ref(session, payload.ssh_credential)

    gateway = await repo.create(JobGateway(
        name=payload.name,
        host=payload.host,
        port=payload.port,
        login_user=payload.login_user,
        ssh_credential=payload.ssh_credential,
        scope=scope,
        priority=payload.priority,
        remark=payload.remark,
        created_by=user.id,
    ))
    await session.commit()
    logger.info(
        "Gateway created",
        extra={"gateway_id": gateway.id, "gateway_name": gateway.name},
    )
    return gateway


async def update_gateway(
    session: AsyncSession, gateway_id: int, payload: GatewayUpdate,
) -> JobGateway:
    gateway = await get_gateway(session, gateway_id)
    data = payload.model_dump(exclude_unset=True)
    new_name = data.get("name")
    if new_name and new_name != gateway.name:
        if await JobGatewayRepo(session).get_by_name(new_name):
            raise ConflictError("JobGateway", f"name '{new_name}' already exists")
    if "scope" in data:
        _validate_scope(data["scope"] or {})
    credential_name = data.get("ssh_credential", gateway.ssh_credential)
    if credential_name:
        await credential_service.resolve_credential_ref(session, credential_name)
    for key, value in data.items():
        setattr(gateway, key, value)
    await JobGatewayRepo(session).update(gateway)
    await session.commit()
    logger.info("Gateway updated", extra={"gateway_id": gateway.id})
    return gateway


async def delete_gateway(session: AsyncSession, gateway_id: int) -> None:
    gateway = await get_gateway(session, gateway_id)
    await JobGatewayRepo(session).delete(gateway)
    await session.commit()
    logger.info("Gateway deleted", extra={"gateway_id": gateway_id})


async def gateway_payload_for_host(
    session: AsyncSession, host: dict, gateways: list[JobGateway] | None = None,
) -> dict | None:
    """算出这台机器该走的中转路径，组装成 dispatch 里 target.gateway 的结构。

    返回 None = 直连（没有任何网关的 scope 覆盖它）。
    """
    active = gateways if gateways is not None else await JobGatewayRepo(session).list_active()
    gateway = pick_gateway(active, host)
    if gateway is None:
        return None
    ssh_key_ref = None
    if gateway.ssh_credential:
        ssh_key_ref = await credential_service.resolve_credential_ref(
            session, gateway.ssh_credential
        )
    return {
        "name": gateway.name,
        "host": gateway.host,
        "port": gateway.port,
        "ssh_user": gateway.login_user,
        "ssh_key_ref": ssh_key_ref,
    }


async def reachability(
    session: AsyncSession,
    model_codes: list[str] | None = None,
    limit: int = 200,
) -> list[dict]:
    """主机可达性视图：凭据是否解析得到 + 走哪条路 + 缺什么。

    只覆盖 running 的机型——非 running 本来就被执行态硬校验拦在门外，
    列进来只会淹没真正的配置缺口。
    """
    codes = model_codes or list(REACHABILITY_MODEL_CODES)
    rows = await session.execute(
        select(CmdbResource, CmdbModel.code)
        .join(CmdbModel, CmdbResource.model_id == CmdbModel.id)
        .where(
            CmdbModel.code.in_(codes),
            CmdbResource.deleted_at.is_(None),
            CmdbResource.status == "running",
        )
        .order_by(CmdbResource.name)
        .limit(limit)
    )
    hosts: list[dict] = []
    for res, code in rows.all():
        fields = res.fields or {}
        hosts.append({
            "resource_id": res.id,
            "name": res.name,
            "ip": next(
                (fields.get(k) for k in ("private_ip", "internal_ip", "ip") if fields.get(k)),
                None,
            ),
            "model_code": code,
            "cloud_account": res.cloud_account,
            "region": res.region,
            "vpc_id": fields.get("vpc_id"),
        })

    creds = await credential_service.resolve_host_credentials(
        session,
        [(h["resource_id"], h["name"], h["cloud_account"], h["region"]) for h in hosts],
    )
    active = await JobGatewayRepo(session).list_active()

    result: list[dict] = []
    for host in hosts:
        cred = creds.get(host["resource_id"])
        gateway = pick_gateway(active, host)
        missing: list[str] = []
        if not host["ip"]:
            missing.append("ip：主机无内网地址，SSH 无法建立")
        if cred is None or cred.error or not cred.vault_ref:
            missing.append(
                "ssh_credential：主机未打凭据标签，且目录中无唯一匹配的默认凭据"
            )
        result.append({
            "resource_id": host["resource_id"],
            "name": host["name"],
            "ip": host["ip"],
            "model_code": host["model_code"],
            "cloud_account": host["cloud_account"],
            "region": host["region"],
            "vpc_id": host["vpc_id"],
            "credential": cred.credential_name if cred else None,
            "credential_ok": bool(cred and not cred.error and cred.vault_ref),
            "gateway": gateway.name if gateway else None,
            # 无网关 = 直连，不是缺口；是否需要中转只有实测才知道
            "gateway_ok": True,
            "missing": missing,
        })
    return result
