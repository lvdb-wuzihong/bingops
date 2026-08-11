"""JWT Token 签发/验证 与密码哈希工具。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
import bcrypt

from bingops.core.config import settings

logger = logging.getLogger(f"bingops.{__name__}")

# ── 密码哈希 ──────────────────────────────────────────────────────────────────


def hash_password(plain: str) -> str:
    """对明文密码进行 bcrypt 哈希。"""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """验证明文密码是否与哈希匹配。"""
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


# ── JWT Token ─────────────────────────────────────────────────────────────────


def create_access_token(
    subject: str,
    *,
    extra: dict[str, Any] | None = None,
) -> str:
    """签发 access_token（短有效期）。

    Args:
        subject: 用户 ID。
        extra: 额外 payload 字段（如 roles, permissions, is_superuser）。

    Returns:
        编码后的 JWT 字符串。
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload: dict[str, Any] = {
        "sub": subject,
        "exp": expire,
        "type": "access",
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(subject: str) -> str:
    """签发 refresh_token（长有效期）。

    Args:
        subject: 用户 ID。

    Returns:
        编码后的 JWT 字符串。
    """
    expire = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
    payload: dict[str, Any] = {
        "sub": subject,
        "exp": expire,
        "type": "refresh",
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def verify_token(token: str) -> dict[str, Any]:
    """验证并解码 JWT Token。

    Args:
        token: JWT 字符串。

    Returns:
        解码后的 payload 字典。

    Raises:
        JWTError: Token 无效或已过期。
    """
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
        return payload
    except JWTError as e:
        logger.warning("Token verification failed", extra={"error": str(e)})
        raise


def build_token_payload(user_id: str, roles: list[str], permissions: list[str], is_superuser: bool) -> dict[str, Any]:
    """构建 Token 中的业务字段。

    Args:
        user_id: 用户 ID。
        roles: 角色 code 列表。
        permissions: 权限 code 列表。
        is_superuser: 是否超级管理员。

    Returns:
        用于 create_access_token 的 extra 字典。
    """
    return {
        "roles": roles,
        "permissions": permissions,
        "is_superuser": is_superuser,
    }
