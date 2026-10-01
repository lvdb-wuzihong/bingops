"""凭据目录 API（平台级：任务、监控数据源等模块共用）。

只管理 Vault 引用与元数据；接口不返回、也不接收任何明文凭据值。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.api.dependencies import get_db_session, require_permission
from bingops.core.response import paginated_response, success_response
from bingops.models.credential import Credential
from bingops.models.user import User
from bingops.schemas.credential import (
    CredentialCreate,
    CredentialResponse,
    CredentialUpdate,
)
from bingops.services import credential_service

router = APIRouter(prefix="/api/v1/credentials", tags=["credentials"])


def _to_response(credential: Credential) -> dict:
    return CredentialResponse(
        id=credential.id,
        name=credential.name,
        kind=credential.kind,
        vault_path=credential.vault_path,
        vault_field=credential.vault_field,
        cloud_account=credential.cloud_account,
        region=credential.region,
        is_default=credential.is_default,
        verify_state=credential.verify_state,
        last_verified_at=credential.last_verified_at,
        remark=credential.remark,
        is_active=credential.is_active,
        created_by=credential.created_by,
        created_at=credential.created_at,
        updated_at=credential.updated_at,
    ).model_dump(mode="json")


@router.get("")
async def list_credentials(
    kind: str | None = Query(None, description="ssh_key|cloud_ak|db_password|api_token|kubeconfig"),
    keyword: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("credential:list"),
):
    """凭据列表（按 kind 过滤；执行表单的下拉数据源）。"""
    credentials, total = await credential_service.list_credentials(
        session, kind=kind, keyword=keyword, page=page, page_size=page_size,
    )
    items = [_to_response(c) for c in credentials]
    return paginated_response(items, total, page, page_size)


@router.post("", status_code=201)
async def create_credential(
    payload: CredentialCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = require_permission("credential:create"),
):
    """新建凭据条目：只登记 Vault 引用，含明文特征串会被拒绝。"""
    credential = await credential_service.create_credential(session, payload, current_user)
    return success_response(
        data=_to_response(credential), message="Credential created", http_status=201,
    )


@router.get("/{credential_id}")
async def get_credential(
    credential_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("credential:get"),
):
    """凭据详情。"""
    credential = await credential_service.get_credential(session, credential_id)
    return success_response(data=_to_response(credential))


@router.get("/{credential_id}/usage")
async def get_credential_usage(
    credential_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("credential:get"),
):
    """引用反查：这台钥匙被哪些主机/runbook 用着（轮换前必看）。"""
    credential = await credential_service.get_credential(session, credential_id)
    usage = await credential_service.usage_of(session, credential)
    return success_response(data=usage)


@router.put("/{credential_id}")
async def update_credential(
    credential_id: int,
    payload: CredentialUpdate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("credential:update"),
):
    """更新凭据条目（is_active=false 即停用，保留引用历史）。"""
    credential = await credential_service.update_credential(session, credential_id, payload)
    return success_response(data=_to_response(credential), message="Credential updated")


@router.delete("/{credential_id}")
async def delete_credential(
    credential_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("credential:delete"),
):
    """删除凭据条目（仍被引用时拒绝，改用停用）。"""
    await credential_service.delete_credential(session, credential_id)
    return success_response(message="Credential deleted")
