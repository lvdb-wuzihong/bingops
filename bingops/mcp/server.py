"""bingops-mcp 服务器组装。

FastMCP 挂载进现有 FastAPI 进程（streamable-http /mcp，无状态模式），
不新增部署单元；工具注册见 tools/ 包（import 副作用）。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(f"bingops.{__name__}")

mcp = FastMCP("bingops", stateless_http=True)
"""bingops-mcp 实例：AI agent 的目录与 join 层（设计见 docs/ai-agent-mcp-design.md §4）。"""


def mount_mcp(app: FastAPI) -> None:
    """挂载 /mcp 端点；须在 app 组装期调用一次（lifespan 之前）。"""
    from bingops.mcp import tools  # noqa: F401  import 副作用完成工具注册

    app.mount("/mcp", mcp.streamable_http_app())
    logger.info("bingops-mcp mounted", extra={"path": "/mcp", "stateless": True})


@asynccontextmanager
async def mcp_lifespan() -> AsyncIterator[None]:
    """驱动 streamable-http session manager（在 lifespan 内进入，请求前启动）。"""
    async with mcp.session_manager.run():
        yield
