"""JWT Token 签发/验证 与密码哈希工具。"""

from __future__ import annotations

import base64
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import pyotp
from cryptography.fernet import Fernet, InvalidToken
from jose import JWTError, jwt

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
    expire = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
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
    expire = datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)
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


def build_token_payload(
    user_id: str, roles: list[str], permissions: list[str], is_superuser: bool,
) -> dict[str, Any]:
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


# ── MFA（TOTP）──────────────────────────────────────────────────────────────

# 短期票据类型：已绑定用户登录校验 / 强制绑定流程（仅能访问绑定端点）
MFA_TICKET_TYPE = "mfa"
MFA_SETUP_TICKET_TYPE = "mfa_setup"
MFA_TICKET_EXPIRE_MINUTES = 5


def generate_totp_secret() -> str:
    """生成 base32 TOTP secret（明文仅出现在绑定响应中，不落库）。"""
    return pyotp.random_base32()


def build_totp_uri(secret: str, username: str, issuer: str = "BingOps") -> str:
    """生成 otpauth:// URI（前端渲染二维码，扫码即绑定）。"""
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer)


def verify_totp(secret: str, code: str) -> bool:
    """校验 6 位 TOTP（±1 个时间步容差，应对客户端时钟偏移）。"""
    if not secret or not code:
        return False
    return pyotp.TOTP(secret).verify(code.strip(), valid_window=1)


def _derive_fernet(seed: str) -> Fernet:
    """由种子密钥派生 Fernet 密钥（AES + HMAC，密文可安全落库）。"""
    key = base64.urlsafe_b64encode(hashlib.sha256(seed.encode()).digest())
    return Fernet(key)


def _encryption_seed() -> str:
    """当前加密种子：独立 MFA 密钥优先，未配置时回退 secret_key（兼容旧部署）。"""
    return settings.mfa_encryption_key or settings.secret_key


def encrypt_secret(plain: str) -> str:
    """用当前密钥加密 TOTP secret（轮换后新写入一律用新密钥）。"""
    return _derive_fernet(_encryption_seed()).encrypt(plain.encode()).decode()


def decrypt_secret_with_rotation(token: str) -> tuple[str, bool]:
    """解密 TOTP secret，返回 (明文, 是否需要重加密)。

    先用当前密钥，失败再尝试旧密钥；旧密钥解开时标记需重写，
    由调用方在登录成功后用新密钥覆盖（无感轮换）。

    Raises:
        InvalidToken: 当前与旧密钥均无法解密（密钥丢失/数据损坏）。
    """
    try:
        return _derive_fernet(_encryption_seed()).decrypt(token.encode()).decode(), False
    except InvalidToken:
        pass
    old = settings.mfa_encryption_key_old
    if old:
        try:
            return _derive_fernet(old).decrypt(token.encode()).decode(), True
        except InvalidToken:
            pass
    raise InvalidToken("MFA secret cannot be decrypted with current or old key")


def decrypt_secret(token: str) -> str:
    """兼容接口：仅返回明文（需要重加密判断时用 decrypt_secret_with_rotation）。"""
    return decrypt_secret_with_rotation(token)[0]


def create_mfa_ticket(subject: str, *, purpose: str) -> str:
    """签发 MFA 短期票据（默认 5 分钟，type 与 access/refresh 隔离）。"""
    expire = datetime.now(UTC) + timedelta(minutes=MFA_TICKET_EXPIRE_MINUTES)
    payload: dict[str, Any] = {"sub": subject, "exp": expire, "type": purpose}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def verify_mfa_ticket(token: str, *, purpose: str) -> int:
    """验证 MFA 票据并返回 user_id。

    Raises:
        JWTError: 票据无效/过期/类型不符。
        ValueError: sub 非法。
    """
    payload = verify_token(token)
    if payload.get("type") != purpose:
        raise JWTError("Invalid MFA ticket type")
    return int(payload.get("sub", ""))
