"""角色管理 API 路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.api.dependencies import get_db_session, require_permission
from bingops.core.response import success_response
from bingops.models.user import User
from bingops.schemas.role import (
    PermissionResponse,
    RoleCreate,
    RolePermissionAssign,
    RoleResponse,
    RoleUpdate,
)
from bingops.services import role_service

router = APIRouter(prefix="/api/v1/roles", tags=["roles"])


@router.get("")
async def list_roles(
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("role:list"),
):
    """查询角色列表。"""
    roles = await role_service.list_roles(session)
    items = [
        RoleResponse(
            id=r.id,
            code=r.code,
            name=r.name,
            description=r.description,
            is_system=r.is_system,
            created_at=r.created_at,
            updated_at=r.updated_at,
            permissions=[p.code for p in r.permissions],
        ).model_dump(mode="json")
        for r in roles
    ]
    return success_response(data=items)


@router.post("", status_code=201)
async def create_role(
    payload: RoleCreate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("role:create"),
):
    """创建角色。"""
    role = await role_service.create_role(session, payload)
    data = RoleResponse(
        id=role.id,
        code=role.code,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
        created_at=role.created_at,
        updated_at=role.updated_at,
    )
    return success_response(data=data.model_dump(mode="json"), message="Role created", http_status=201)


@router.get("/{role_id}")
async def get_role(
    role_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("role:list"),
):
    """获取角色详情。"""
    role = await role_service.get_role(session, role_id)
    data = RoleResponse(
        id=role.id,
        code=role.code,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
        created_at=role.created_at,
        updated_at=role.updated_at,
        permissions=[p.code for p in role.permissions],
    )
    return success_response(data=data.model_dump(mode="json"))


@router.put("/{role_id}")
async def update_role(
    role_id: int,
    payload: RoleUpdate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("role:update"),
):
    """更新角色信息。"""
    role = await role_service.update_role(session, role_id, payload)
    data = RoleResponse(
        id=role.id,
        code=role.code,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
        created_at=role.created_at,
        updated_at=role.updated_at,
    )
    return success_response(data=data.model_dump(mode="json"))


@router.delete("/{role_id}")
async def delete_role(
    role_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("role:delete"),
):
    """删除角色（系统角色不可删）。"""
    await role_service.delete_role(session, role_id)
    return success_response(message="Role deleted")


@router.put("/{role_id}/permissions")
async def assign_permissions(
    role_id: int,
    payload: RolePermissionAssign,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("role:update"),
):
    """为角色分配权限。"""
    role = await role_service.assign_permissions(session, role_id, payload.permission_codes)
    data = RoleResponse(
        id=role.id,
        code=role.code,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
        created_at=role.created_at,
        updated_at=role.updated_at,
        permissions=[p.code for p in role.permissions],
    )
    return success_response(data=data.model_dump(mode="json"))


@router.get("/permissions/all")
async def list_permissions(
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("role:list"),
):
    """查询所有权限清单（前端渲染菜单用）。"""
    permissions = await role_service.list_permissions(session)
    items = [
        PermissionResponse(id=p.id, code=p.code, name=p.name, description=p.description).model_dump(mode="json")
        for p in permissions
    ]
    return success_response(data=items)
