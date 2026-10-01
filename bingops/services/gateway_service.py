"""中转网关业务层：按 VPC 选路与路径组装（v35 单一维度）。

网关描述的是"这台机器要怎么才被连到"，属于网络拓扑事实；因此选路必须在
执行期由机器所属 VPC 算出来，而不是让每个 runbook 声明一次（漏声明的后果是
SSH 超时，现象像 playbook 写错，排查成本极高）。执行面也可用 `gateway_name`
强制指定。设计见 §5.2。
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.core.exceptions import ConflictError, NotFoundError, ValidationError
from bingops.models.job_gateway import JobGateway
from bingops.models.user import User
from bingops.repositories.job_gateway_repo import JobGatewayRepo
from bingops.schemas.job_gateway import GatewayCreate, GatewayUpdate
from bingops.services import credential_service

logger = logging.getLogger(f"bingops.{__name__}")


def _validate_vpc_ids(vpc_ids: list) -> list[str]:
    """VPC 列表校验：去重去空后必须非空。

    空 vpc_ids 不接管任何机器——刻意不做"全局兜底网关"，一个误配的条目
    就把全部流量接管过去，那种故障比连不上更难查。
    """
    cleaned: list[str] = []
    for raw in vpc_ids or []:
        value = str(raw).strip()
        if value and value not in cleaned:
            cleaned.append(value)
    if not cleaned:
        raise ValidationError(
            "vpc_ids is required: 该网关要接管哪些 VPC？留空它匹配不到任何机器"
            "（VPC 可从 CMDB 的 aliyun_vpc / gcp_vpc 列表选，不要手打）"
        )
    return cleaned


async def _ensure_vpc_exclusive(
    session: AsyncSession, vpc_ids: list[str], *, exclude_id: int | None = None,
) -> None:
    """一个 VPC 只能由一条启用网关接管。

    多网关声明同一 VPC 时"谁生效"取决于遍历顺序，而 runner 并没有跳板故障
    转移能力——这种多义没有正当用途，必须在写入时拒掉，而不是留个 priority
    让人去猜规则。
    """
    rows = await session.execute(
        select(JobGateway.id, JobGateway.name, JobGateway.vpc_ids)
        .where(JobGateway.is_active.is_(True))
    )
    wanted = set(vpc_ids)
    for gid, gname, gvpcs in rows.all():
        if exclude_id is not None and gid == exclude_id:
            continue
        dup = wanted & set(gvpcs or [])
        if dup:
            raise ConflictError(
                "JobGateway",
                f"VPC {sorted(dup)} 已由网关 '{gname}' 接管；一个 VPC 只允许一条网关，"
                "换跳板请改那条记录，或先把它停用",
            )


def pick_gateway(gateways: list[JobGateway], vpc_id: str | None) -> JobGateway | None:
    """按机器所属 VPC 取网关；`None` = 直连（VPC 未登记或无人接管）。

    排序按 name 保证确定性；因 VPC 互斥约束，正常情况下最多一条命中。
    """
    if not vpc_id:
        return None
    for gateway in sorted(gateways, key=lambda g: g.name):
        if vpc_id in (gateway.vpc_ids or []):
            return gateway
    return None


def _payload(gateway: JobGateway, ssh_key_ref: str | None) -> dict:
    return {
        "name": gateway.name,
        "host": gateway.host,
        "port": gateway.port,
        "ssh_user": gateway.login_user,
        "ssh_key_ref": ssh_key_ref,
    }


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
    vpc_ids = _validate_vpc_ids(payload.vpc_ids)
    await _ensure_vpc_exclusive(session, vpc_ids)
    if payload.ssh_credential:
        # 引用必须存在于凭据目录且是 ssh_key：把"配了但取不到钥匙"留在写入口
        await credential_service.resolve_credential_ref(session, payload.ssh_credential)

    gateway = await repo.create(JobGateway(
        name=payload.name,
        host=payload.host,
        port=payload.port,
        login_user=payload.login_user,
        ssh_credential=payload.ssh_credential,
        vpc_ids=vpc_ids,
        remark=payload.remark,
        created_by=user.id,
    ))
    await session.commit()
    logger.info(
        "Gateway created",
        extra={"gateway_id": gateway.id, "gateway_name": gateway.name, "vpc_ids": vpc_ids},
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
    if "vpc_ids" in data:
        data["vpc_ids"] = _validate_vpc_ids(data["vpc_ids"])
        # 停用中的记录不占位：先停用旧网关再建新网关的顺序不该被互斥校验卡住
        if data.get("is_active", gateway.is_active):
            await _ensure_vpc_exclusive(session, data["vpc_ids"], exclude_id=gateway_id)
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

    host 只需带 `vpc_id`（取自 cmdb_resources.fields.vpc_id）；返回 None = 直连。
    """
    active = gateways if gateways is not None else await JobGatewayRepo(session).list_active()
    gateway = pick_gateway(active, host.get("vpc_id"))
    if gateway is None:
        return None
    ssh_key_ref = None
    if gateway.ssh_credential:
        ssh_key_ref = await credential_service.resolve_credential_ref(
            session, gateway.ssh_credential
        )
    return _payload(gateway, ssh_key_ref)


async def gateway_payload_by_name(session: AsyncSession, name: str) -> dict:
    """按名字取网关路径（执行面强制指定时用）；不存在或停用即 400。"""
    gateway = await JobGatewayRepo(session).get_by_name(name)
    if gateway is None or not gateway.is_active:
        raise ValidationError(f"gateway '{name}' not found or inactive")
    ssh_key_ref = None
    if gateway.ssh_credential:
        ssh_key_ref = await credential_service.resolve_credential_ref(
            session, gateway.ssh_credential
        )
    return _payload(gateway, ssh_key_ref)
