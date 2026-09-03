"""MCP 工具层共享设施：会话绑定、输出裁剪、脱敏、错误翻译、调用日志。

约定见 .qoder/skills/bingops-mcp-tools（SKILL.md §2/§5/§6/§7/§9）。
"""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bingops.core.config import settings
from bingops.core.exceptions import BingOpsError

logger = logging.getLogger(f"bingops.{__name__}")

# 默认脱敏 key（小写包含即命中）
DEFAULT_SENSITIVE_KEYS = (
    "password", "secret", "token", "credential",
    "access_key", "private_key", "connection_string",
)

# ── 会话绑定 ──────────────────────────────────────────────────────────────────

_session_factory: async_sessionmaker | None = None


def bind_session_factory(factory: async_sessionmaker) -> None:
    """由 main.py lifespan 注入会话工厂（MCP 工具不走 FastAPI DI）。"""
    global _session_factory
    _session_factory = factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """工具内获取数据库会话；commit 由 service 层负责（与现有分层一致）。"""
    if _session_factory is None:
        raise BingOpsError(
            "MCP session factory not bound: BINGOPS_MCP_ENABLED lifecycle not started",
            code=50001, http_status=500,
        )
    async with _session_factory() as session:
        yield session


# ── 输出裁剪与脱敏 ────────────────────────────────────────────────────────────

def clamp_limit(limit: int | None, default: int = 20, maximum: int = 100) -> int:
    """列表上限收敛：超限静默收敛而非报错（SKILL §5）。"""
    if limit is None or limit <= 0:
        return default
    return min(limit, maximum)


def pick_fields(data: dict, whitelist: list[str]) -> dict:
    """字段白名单裁剪（SKILL §5），datetime 统一 ISO 8601。"""
    return {k: _iso(data[k]) for k in whitelist if k in data}


def _iso(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def redact(data: Any, sensitive_keys: tuple[str, ...] = DEFAULT_SENSITIVE_KEYS) -> Any:
    """递归脱敏：命中敏感 key（小写包含）的值替换为 ***（SKILL §7）。"""
    if isinstance(data, dict):
        return {
            k: (
                "***"
                if any(s in str(k).lower() for s in sensitive_keys)
                else redact(v, sensitive_keys)
            )
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [redact(v, sensitive_keys) for v in data]
    return data


def brief(value: Any, max_len: int = 120) -> str:
    """日志参数摘要：字符串化并截断（SKILL §9）。"""
    text = str(value)
    return text if len(text) <= max_len else text[:max_len] + "..."


# ── 错误翻译 ──────────────────────────────────────────────────────────────────

# 业务码 → (工具层错误码, 默认 next_step 提示)
_ERROR_MAP: dict[int, tuple[str, str]] = {
    40001: ("invalid_request", "fix the parameters according to the message and retry"),
    40101: ("permission_denied", "check that the MCP token binds the ai_agent read-only role"),
    40301: ("permission_denied", "this action requires extra permission or a disabled switch"),
    40401: ("not_found", "locate the correct id first via the matching search_/list_ tool"),
    40901: ("conflict", "refresh current state via the matching list tool and retry"),
    50001: ("internal", "retry later; if persistent check platform logs"),
    50201: ("external", "retry later; if persistent check the upstream service"),
}


def translate_error(exc: Exception) -> dict:
    """异常 → 结构化工具错误（SKILL §6）：code + message + next_step。"""
    if isinstance(exc, BingOpsError):
        error_code, hint = _ERROR_MAP.get(exc.code, ("internal", "retry later"))
        return {
            "error": {
                "code": error_code,
                "message": exc.message,
                "next_step": hint,
            },
        }
    return {
        "error": {
            "code": "internal",
            "message": str(exc) or exc.__class__.__name__,
            "next_step": "retry later; if persistent check platform logs",
        },
    }


# ── agent 系统账号 ────────────────────────────────────────────────────────────

async def get_agent_user(session: AsyncSession):
    """写工具操作者：BINGOPS_MCP_AGENT_USER_ID 指定的系统账号。"""
    if settings.mcp_agent_user_id <= 0:
        raise BingOpsError(
            "MCP agent user not configured: set BINGOPS_MCP_AGENT_USER_ID",
            code=40001, http_status=422,
        )
    from bingops.repositories.user_repo import UserRepo

    user = await UserRepo(session).get_by_id(settings.mcp_agent_user_id)
    if user is None or not user.is_active:
        raise BingOpsError(
            f"MCP agent user {settings.mcp_agent_user_id} not found or inactive",
            code=40401, http_status=404,
        )
    return user


# ── 工具调用日志 + 错误翻译装饰器 ─────────────────────────────────────────────

def mcp_tool_logging(tool_name: str) -> Callable:
    """工具统一包装：入口/出口结构化日志 + 异常翻译为 {"error": ...}（SKILL §6/§9）。"""

    def deco(fn: Callable[..., Awaitable[dict]]) -> Callable[..., Awaitable[dict]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> dict:
            args_summary = brief({
                k: ("***" if any(s in k.lower() for s in DEFAULT_SENSITIVE_KEYS) else brief(v, 60))
                for k, v in kwargs.items()
            }, 200)
            logger.info("MCP tool called", extra={"tool": tool_name, "args": args_summary})
            start = time.monotonic()
            try:
                result = await fn(*args, **kwargs)
            except BingOpsError as exc:
                elapsed = round((time.monotonic() - start) * 1000, 1)
                logger.warning(
                    "MCP tool failed",
                    extra={"tool": tool_name, "elapsed_ms": elapsed, "error": exc.message},
                )
                return translate_error(exc)
            except Exception as exc:
                elapsed = round((time.monotonic() - start) * 1000, 1)
                logger.error(
                    "MCP tool crashed",
                    extra={"tool": tool_name, "elapsed_ms": elapsed, "error": str(exc)},
                    exc_info=True,
                )
                return translate_error(exc)
            elapsed = round((time.monotonic() - start) * 1000, 1)
            logger.info("MCP tool done", extra={"tool": tool_name, "elapsed_ms": elapsed})
            return result

        return wrapper

    return deco
