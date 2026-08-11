"""飞书 OAuth2 SSO 认证提供者。

流程：
1. 前端获取飞书授权页 URL → 用户在飞书授权
2. 飞书回调带 code → 后端用 code 换 user_access_token
3. 获取飞书用户信息 → 查找/自动创建平台用户 → 签发平台 JWT
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.core.config import feishu_settings
from bingops.core.exceptions import AuthenticationError, ExternalServiceError
from bingops.models.role import Role
from bingops.models.user import User
from bingops.models.user_role import UserRole

logger = logging.getLogger(f"bingops.{__name__}")


class FeishuAuthProvider:
    """飞书 OAuth2 SSO 认证。

    credentials 格式: {"code": "飞书授权码"}
    """

    async def authenticate(self, session: AsyncSession, credentials: dict) -> User:
        code = credentials.get("code", "")
        if not code:
            raise AuthenticationError("Missing authorization code")

        feishu_user = await self._get_feishu_user_info(code)
        user = await self._find_or_create_user(session, feishu_user)

        if not user.is_active:
            raise AuthenticationError("Account is disabled")

        return user

    async def get_authorize_url(self) -> str:
        """生成飞书授权页重定向 URL。"""
        return (
            f"{feishu_settings.authorize_url}"
            f"?app_id={feishu_settings.app_id}"
            f"&redirect_uri={feishu_settings.redirect_uri}"
            f"&response_type=code"
        )

    async def _get_app_access_token(self) -> str:
        """获取飞书应用 access_token。"""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                feishu_settings.app_token_url,
                json={
                    "app_id": feishu_settings.app_id,
                    "app_secret": feishu_settings.app_secret,
                },
                timeout=10,
            )
            data = resp.json()

        if data.get("code") != 0:
            logger.error("Failed to get app_access_token", extra={"response": data})
            raise ExternalServiceError("feishu", "Failed to get app_access_token")

        return data["app_access_token"]

    async def _get_user_access_token(self, code: str) -> str:
        """用授权码换取飞书 user_access_token。"""
        app_token = await self._get_app_access_token()

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                feishu_settings.user_token_url,
                headers={"Authorization": f"Bearer {app_token}"},
                json={"grant_type": "authorization_code", "code": code},
                timeout=10,
            )
            data = resp.json()

        if data.get("code") != 0:
            logger.error("Failed to get user_access_token", extra={"response": data})
            raise ExternalServiceError("feishu", "Failed to exchange authorization code")

        return data["data"]["access_token"]

    async def _get_feishu_user_info(self, code: str) -> dict[str, Any]:
        """获取飞书用户信息。"""
        user_token = await self._get_user_access_token(code)

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                feishu_settings.user_info_url,
                headers={"Authorization": f"Bearer {user_token}"},
                timeout=10,
            )
            data = resp.json()

        if data.get("code") != 0:
            logger.error("Failed to get feishu user info", extra={"response": data})
            raise ExternalServiceError("feishu", "Failed to get user info")

        user_data = data["data"]
        return {
            "open_id": user_data["open_id"],
            "union_id": user_data.get("union_id"),
            "name": user_data.get("name", ""),
            "email": user_data.get("email", ""),
            "avatar_url": user_data.get("avatar_url", ""),
        }

    async def _find_or_create_user(self, session: AsyncSession, feishu_user: dict[str, Any]) -> User:
        """按 feishu_open_id 查找用户，不存在则自动创建。"""
        open_id = feishu_user["open_id"]

        result = await session.execute(
            select(User).where(User.feishu_open_id == open_id)
        )
        user = result.scalar_one_or_none()

        if user is not None:
            # 更新飞书信息
            user.display_name = feishu_user["name"] or user.display_name
            user.avatar_url = feishu_user["avatar_url"] or user.avatar_url
            await session.flush()
            logger.info("Feishu user logged in", extra={"user_id": user.id})
            return user

        # 自动开户
        email = feishu_user["email"]
        username = email.split("@")[0] if email else f"feishu_{open_id[:8]}"

        user = User(
            username=username,
            email=email or f"{open_id}@feishu.local",
            display_name=feishu_user["name"],
            auth_source="feishu",
            feishu_open_id=open_id,
            feishu_union_id=feishu_user.get("union_id"),
            avatar_url=feishu_user["avatar_url"],
        )
        session.add(user)
        await session.flush()

        # 分配默认 viewer 角色
        result = await session.execute(
            select(Role).where(Role.code == "viewer")
        )
        viewer_role = result.scalar_one_or_none()
        if viewer_role:
            session.add(UserRole(user_id=user.id, role_id=viewer_role.id))
            await session.flush()

        logger.info("Feishu user auto-created", extra={"user_id": user.id, "username": username})
        return user
