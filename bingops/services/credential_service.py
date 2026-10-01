"""凭据目录业务层。

纪律：本层只管理「引用与元数据」，绝不接触明文凭据值；bingops 也不连接 Vault，
取真值由 runner 负责（单一出口）。入口有明文特征串校验，防止有人把私钥整段粘进来。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.core.exceptions import ConflictError, NotFoundError, ValidationError
from bingops.models.cmdb.resource import CmdbResource
from bingops.models.cmdb.tag import CmdbResourceTag
from bingops.models.credential import (
    CREDENTIAL_KINDS,
    HOST_CREDENTIAL_TAG_KEY,
    HOST_USER_TAG_KEY,
    Credential,
)
from bingops.models.jobs import Runbook
from bingops.models.user import User
from bingops.repositories.credential_repo import CredentialRepo
from bingops.schemas.credential import CredentialCreate, CredentialUpdate

logger = logging.getLogger(f"bingops.{__name__}")

__all__ = ["HOST_CREDENTIAL_TAG_KEY"]  # 供 job_service 解析目标机凭据时复用

# 明文凭据特征串：出现在引用字段里说明用法错了，入口直接挡掉
_PLAINTEXT_MARKERS = ("-----BEGIN", "PRIVATE KEY", "SSH-RSA AAAA")


def _validate(kind: str | None, fields: dict) -> None:
    """kind 白名单 + 明文防呆 + path/field 拆分规范。"""
    if kind is not None and kind not in CREDENTIAL_KINDS:
        raise ValidationError(
            f"unknown kind '{kind}' (supported: {list(CREDENTIAL_KINDS)})"
        )
    for name, value in fields.items():
        if value is None:
            continue
        upper = str(value).upper()
        hit = next((m for m in _PLAINTEXT_MARKERS if m in upper), None)
        if hit:
            raise ValidationError(
                f"{name} 疑似含明文凭据（命中 '{hit}'）；"
                "凭据目录只允许存 Vault 引用，真值请放 Vault"
            )
    path = fields.get("vault_path")
    if path and "#" in path:
        raise ValidationError(
            "vault_path 不得包含 '#'；请把字段名填到 vault_field（path#field 已拆分存储）"
        )


async def list_credentials(
    session: AsyncSession,
    kind: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Credential], int]:
    return await CredentialRepo(session).list(kind, keyword, page, page_size)


async def create_credential(
    session: AsyncSession, payload: CredentialCreate, user: User,
) -> Credential:
    _validate(payload.kind, {
        "vault_path": payload.vault_path,
        "vault_field": payload.vault_field,
        "remark": payload.remark,
    })
    repo = CredentialRepo(session)
    if await repo.get_by_name(payload.name):
        raise ConflictError("Credential", f"name '{payload.name}' already exists")

    credential = Credential(
        name=payload.name,
        kind=payload.kind,
        vault_path=payload.vault_path,
        vault_field=payload.vault_field,
        cloud_account=payload.cloud_account,
        region=payload.region,
        is_default=payload.is_default,
        remark=payload.remark,
        created_by=user.id,
    )
    credential = await repo.create(credential)
    if credential.is_default:
        await repo.clear_default(credential.kind, except_id=credential.id)
    await session.commit()
    logger.info(
        "Credential created",
        extra={"credential_id": credential.id, "credential_name": credential.name},
    )
    return credential


async def get_credential(session: AsyncSession, credential_id: int) -> Credential:
    credential = await CredentialRepo(session).get_by_id(credential_id)
    if credential is None:
        raise NotFoundError("Credential", str(credential_id))
    return credential


async def get_credential_by_name(session: AsyncSession, name: str) -> Credential:
    credential = await CredentialRepo(session).get_by_name(name)
    if credential is None:
        raise NotFoundError("Credential", name)
    return credential


async def update_credential(
    session: AsyncSession, credential_id: int, payload: CredentialUpdate,
) -> Credential:
    credential = await get_credential(session, credential_id)
    data = payload.model_dump(exclude_unset=True)
    _validate(data.get("kind", credential.kind), {
        name: data.get(name)
        for name in ("vault_path", "vault_field", "remark")
        if name in data
    })
    new_name = data.get("name")
    if new_name and new_name != credential.name:
        if await CredentialRepo(session).get_by_name(new_name):
            raise ConflictError("Credential", f"name '{new_name}' already exists")
    for key, value in data.items():
        setattr(credential, key, value)
    if credential.is_default:
        await CredentialRepo(session).clear_default(credential.kind, except_id=credential.id)
    await CredentialRepo(session).update(credential)
    await session.commit()
    logger.info("Credential updated", extra={"credential_id": credential.id})
    return credential


async def usage_of(session: AsyncSession, credential: Credential) -> dict:
    """引用反查：换钥匙/删条目前必须先看清影响面。"""
    host_rows = await session.execute(
        select(CmdbResource.id, CmdbResource.name)
        .join(CmdbResourceTag, CmdbResourceTag.resource_id == CmdbResource.id)
        .where(
            CmdbResourceTag.tag_key == HOST_CREDENTIAL_TAG_KEY,
            CmdbResourceTag.tag_value == credential.name,
            CmdbResource.deleted_at.is_(None),
        )
        .limit(50)
    )
    hosts = [{"resource_id": rid, "name": name} for rid, name in host_rows.all()]
    host_refs = (
        await session.execute(
            select(func.count())
            .select_from(CmdbResourceTag)
            .where(
                CmdbResourceTag.tag_key == HOST_CREDENTIAL_TAG_KEY,
                CmdbResourceTag.tag_value == credential.name,
            )
        )
    ).scalar() or 0
    runbook_refs = (
        await session.execute(
            select(func.count()).select_from(Runbook).where(
                # 用 jsonb_extract_path_text 取文本：避开 JSONB 索引运算符与 cast 的歧义
                func.jsonb_extract_path_text(Runbook.connection, "ssh_key_ref")
                == credential.name
            )
        )
    ).scalar() or 0
    return {
        "credential_id": credential.id,
        "credential_name": credential.name,
        "host_tag_refs": int(host_refs),
        "runbook_refs": int(runbook_refs),
        "hosts": hosts,
    }


async def delete_credential(session: AsyncSession, credential_id: int) -> None:
    credential = await get_credential(session, credential_id)
    used = await usage_of(session, credential)
    if used["host_tag_refs"] or used["runbook_refs"]:
        raise ConflictError(
            "Credential",
            f"仍被 {used['host_tag_refs']} 台主机标签 / {used['runbook_refs']} 个 runbook 引用，"
            "请改用停用（is_active=false）而不是删除",
        )
    await CredentialRepo(session).delete(credential)
    await session.commit()
    logger.info("Credential deleted", extra={"credential_id": credential_id})


# ── 目标机凭据解析（v31；v32 起供网关可达视图复用同一条链）──────────────


@dataclass
class ResolvedCredential:
    """一台机器的凭据解析结果。`error` 非空表示无法解析，
    由调用方决定是报错（创建执行）还是收集（可达视图）。"""

    login_user: str | None = None
    vault_ref: str | None = None
    credential_name: str | None = None
    error: str | None = None


def vault_ref_of(credential: Credential) -> str:
    """拼 runner 消费的 Vault 引用串（path 或 path#field）。"""
    if credential.vault_field:
        return f"{credential.vault_path}#{credential.vault_field}"
    return credential.vault_path


async def resolve_credential_ref(session: AsyncSession, name: str) -> str:
    """按名字取 Vault 引用（网关自身钥匙用）；不存在或 kind 不符则 400。"""
    credential = await CredentialRepo(session).get_by_name(name)
    if credential is None:
        raise ValidationError(f"credential '{name}' not found in catalog")
    if credential.kind != "ssh_key":
        raise ValidationError(
            f"credential '{name}' is kind '{credential.kind}', expected 'ssh_key'"
        )
    return vault_ref_of(credential)


async def resolve_host_credentials(
    session: AsyncSession,
    hosts: list[tuple[int, str, str | None, str | None]],
    connection: dict | None = None,
) -> dict[int, ResolvedCredential]:
    """逐台解析 (登录用户, Vault 凭据引用)（v33：身份与钥匙都归主机）。

    登录身份：connection.ssh_user（任务声明的身份要求，如高危任务强制低权）
    > 主机标签 ssh_user。**跨用户是常态**——同一把钥匙常被授权给不同主机的
    不同用户，所以“用哪个用户连”属于主机，不属于钥匙；刻意不设平台级默认。

    钥匙材料：主机标签 ssh_credential → 适用范围唯一命中 → 同 kind 的
    is_default → connection.ssh_key_ref（存量兜底，行为不变）。

    多命中不猜；两类缺口（缺身份 / 缺钥匙）合并进同一条报错，
    由调用方决定报 400（创建执行）还是收集（可达视图）。

    hosts 元素 = (resource_id, name, cloud_account, region)。
    """
    repo = CredentialRepo(session)
    fallback_ref = (connection or {}).get("ssh_key_ref")
    fallback_user = (connection or {}).get("ssh_user")

    tagged: dict[int, dict[str, str]] = {}
    ids = [h[0] for h in hosts]
    if ids:
        tag_rows = await session.execute(
            select(
                CmdbResourceTag.resource_id,
                CmdbResourceTag.tag_key,
                CmdbResourceTag.tag_value,
            ).where(
                CmdbResourceTag.resource_id.in_(ids),
                CmdbResourceTag.tag_key.in_(
                    (HOST_CREDENTIAL_TAG_KEY, HOST_USER_TAG_KEY)
                ),
            )
        )
        for rid, key, value in tag_rows.all():
            tagged.setdefault(rid, {})[key] = value

    out: dict[int, ResolvedCredential] = {}
    for rid, host_name, cloud_account, region in hosts:
        tags = tagged.get(rid, {})
        errors: list[str] = []

        # ① 登录身份：任务声明 > 主机标签。无平台级默认（默认 root 会把
        # 漏配置变成高危行为），缺失就是缺口，由可达视图暴露
        login_user = fallback_user or tags.get(HOST_USER_TAG_KEY)
        if not login_user:
            errors.append(
                "lacks ssh_user tag (跨用户环境下登录身份属于主机，"
                "请给主机打 ssh_user 标签)"
            )

        # ② 钥匙材料：主机标签 → 范围唯一命中 → is_default → connection 兜底。
        #    多命中/零命中只报一个原因，不与「has no credential」自相矛盾
        credential: Credential | None = None
        tagged_name = tags.get(HOST_CREDENTIAL_TAG_KEY)
        if tagged_name:
            credential = await repo.get_by_name(tagged_name)
            if credential is None:
                errors.append(
                    f"tag ssh_credential='{tagged_name}' not found in credential catalog"
                )
        else:
            candidates = await repo.resolve("ssh_key", cloud_account, region)
            defaults = [c for c in candidates if c.is_default]
            if len(candidates) == 1:
                credential = candidates[0]
            elif len(defaults) == 1:
                credential = defaults[0]
            elif len(candidates) > 1:
                errors.append(
                    f"matches multiple ssh credentials "
                    f"{[c.name for c in candidates]}; "
                    "set tag ssh_credential to disambiguate"
                )
            elif not fallback_ref:
                errors.append(
                    "has no ssh credential: set tag ssh_credential on the host, "
                    "or register a default credential in the catalog"
                )

        if errors:
            out[rid] = ResolvedCredential(
                error=f"host '{host_name}': " + "; ".join(errors)
            )
            continue

        out[rid] = ResolvedCredential(
            login_user=login_user,
            vault_ref=vault_ref_of(credential) if credential else fallback_ref,
            credential_name=credential.name if credential else None,
        )
    return out
