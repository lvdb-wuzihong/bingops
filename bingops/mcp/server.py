"""bingops-mcp 服务器组装。

FastMCP 挂载进现有 FastAPI 进程（streamable-http /mcp，无状态模式），
不新增部署单元；工具注册见 tools/ 包（import 副作用）。
日志面：HTTP 层（RequestLoggingMiddleware）→ 协议层（本模块 McpProtocolLogger）
→ 工具层（_shared.mcp_tool_logging）→ service 层，共用 request_id 链路。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from bingops.core.config import settings

logger = logging.getLogger(f"bingops.{__name__}")


def _build_transport_security() -> TransportSecuritySettings | None:
    """DNS rebinding 防护：默认开启，allowed_hosts 从配置装配（生产填实际域名/服务名）。"""
    if not settings.mcp_dns_rebinding_protection:
        return None
    hosts = [h.strip() for h in settings.mcp_allowed_hosts.split(",") if h.strip()]
    origins = [o.strip() for o in settings.mcp_allowed_origins.split(",") if o.strip()]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


# streamable_http_path="/"：sub-app 内部路由挂根路径，配合 mount("/mcp") 后整体路径才正确
# （SDK 默认 "/mcp" 会与外层 mount 叠加为 /mcp/mcp）
# json_response=True：JSON 响应模式（SDK 默认 SSE 流式）；无状态数据面无流悬挂风险，编排层更好处理
mcp = FastMCP(
    "bingops",
    stateless_http=True,
    streamable_http_path="/",
    json_response=True,
    transport_security=_build_transport_security(),
)
"""bingops-mcp 实例：AI agent 的目录与 join 层（设计见 docs/ai-agent-mcp-design.md §4）。"""


def _log_protocol_action(body: bytes) -> None:
    """解析 JSON-RPC 批量/单条消息，记录协议动作（只记 method，不打参数）。"""
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return
    messages = payload if isinstance(payload, list) else [payload]
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        method = msg.get("method")
        if method:
            logger.info(
                "MCP protocol message",
                extra={"rpc_method": method, "rpc_id": msg.get("id")},
            )


class McpProtocolLogger:
    """ASGI 包装：记录 MCP 协议动作后重放 body，不改变请求语义（仅拦截 POST）。"""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict,
        receive: Callable[[], Awaitable[dict]],
        send: Callable[[dict], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []
        while True:
            message = await receive()
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        _log_protocol_action(b"".join(chunks))

        buffered = b"".join(chunks)

        async def replay_receive() -> dict:
            nonlocal buffered
            body, buffered = buffered, b""
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay_receive, send)


def mount_mcp(app: FastAPI) -> None:
    """挂载 /mcp 端点；须在 app 组装期调用一次（lifespan 之前）。"""
    from bingops.mcp import tools  # noqa: F401  import 副作用完成工具注册

    app.mount("/mcp", McpProtocolLogger(mcp.streamable_http_app()))
    # _tool_manager 为 FastMCP 内部属性（list_tools 为同步方法），升级 mcp 版本时留意
    tool_names = [t.name for t in mcp._tool_manager.list_tools()]
    logger.info(
        "bingops-mcp mounted",
        extra={"path": "/mcp", "stateless": True, "tools": tool_names},
    )


@asynccontextmanager
async def mcp_lifespan() -> AsyncIterator[None]:
    """驱动 streamable-http session manager（在 lifespan 内进入，请求前启动）。"""
    async with mcp.session_manager.run():
        yield
