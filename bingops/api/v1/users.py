"""用户管理 API 路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.api.dependencies import get_db_session, require_permission
from bingops.core.response import paginated_response, success_response
from bingops.models.user import User
from bingops.schemas.user import UserCreate, UserResponse, UserRoleAssign, UserUpdate
from bingops.services import user_service

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.get("")
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = None,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("user:list"),
):
    """查询用户列表（分页）。"""
    users, total = await user_service.list_users(
        session, page=page, page_size=page_size, keyword=keyword,
    )
    items = [
        UserResponse(
            id=u.id,
            username=u.username,
            email=u.email,
            display_name=u.display_name,
            avatar_url=u.avatar_url,
            auth_source=u.auth_source,
            is_active=u.is_active,
            is_superuser=u.is_superuser,
            last_login_at=u.last_login_at,
            created_at=u.created_at,
            updated_at=u.updated_at,
            roles=[r.code for r in u.roles],
        ).model_dump(mode="json")
        for u in users
    ]
    return paginated_response(items, total, page, page_size)


@router.post("", status_code=201)
async def create_user(
    payload: UserCreate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("user:create"),
):
    """创建用户。"""
    user = await user_service.create_user(session, payload)
    data = UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        display_name=user.display_name,
        auth_source=user.auth_source,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )
    return success_response(data=data.model_dump(mode="json"), message="User created", http_status=201)


@router.get("/{user_id}")
async def get_user(
    user_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("user:list"),
):
    """获取用户详情。"""
    user = await user_service.get_user(session, user_id)
    data = UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        auth_source=user.auth_source,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        updated_at=user.updated_at,
        roles=[r.code for r in user.roles],
    )
    return success_response(data=data.model_dump(mode="json"))


@router.put("/{user_id}")
async def update_user(
    user_id: int,
    payload: UserUpdate,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("user:update"),
):
    """更新用户信息。"""
    user = await user_service.update_user(session, user_id, payload)
    data = UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        display_name=user.display_name,
        auth_source=user.auth_source,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )
    return success_response(data=data.model_dump(mode="json"))


@router.delete("/{user_id}")
async def delete_user(
    user_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("user:delete"),
):
    """删除用户。"""
    await user_service.delete_user(session, user_id)
    return success_response(message="User deleted")


@router.put("/{user_id}/roles")
async def assign_roles(
    user_id: int,
    payload: UserRoleAssign,
    session: AsyncSession = Depends(get_db_session),
    _user: User = require_permission("user:assign_role"),
):
    """为用户分配角色。"""
    user = await user_service.assign_roles(session, user_id, payload.role_codes)
    data = UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        display_name=user.display_name,
        auth_source=user.auth_source,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        created_at=user.created_at,
        updated_at=user.updated_at,
        roles=[r.code for r in user.roles],
    )
    return success_response(data=data.model_dump(mode="json"))
