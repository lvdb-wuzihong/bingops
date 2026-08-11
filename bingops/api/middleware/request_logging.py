"""请求链路日志中间件。

职责：
- 为每个 HTTP 请求分配 request_id（透传 X-Request-ID 请求头）
- 记录请求出入口（方法/路径/状态码/耗时），健康检查除外
- 未捕获异常在重新抛出前记录完整堆栈，保证 K8S 日志可追溯
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from bingops.core.logging import request_id_var

logger = logging.getLogger(__name__)

# 探针路径高频访问，不记录出入口日志
_SKIP_PATHS = frozenset({"/api/health"})


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """请求日志与 request_id 分配。"""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        request_id_var.set(request_id)

        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.error(
                "Unhandled exception during request processing",
                exc_info=True,
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": duration_ms,
                },
            )
            raise

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers["X-Request-ID"] = request_id

        if request.url.path not in _SKIP_PATHS:
            logger.info(
                "Request completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                },
            )
        return response
