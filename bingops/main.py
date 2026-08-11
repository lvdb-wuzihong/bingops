"""BingOps 运维平台 FastAPI 应用入口。

启动方式：
    uvicorn bingops.main:app --reload --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bingops.api import dependencies
from bingops.api.middleware.request_logging import RequestLoggingMiddleware
from bingops.api.v1 import auth, roles, tickets, users
from bingops.api.v1.cmdb import apps as cmdb_apps
from bingops.api.v1.cmdb import changes as cmdb_changes
from bingops.api.v1.cmdb import models as cmdb_models
from bingops.api.v1.cmdb import relationships as cmdb_relationships
from bingops.api.v1.cmdb import resources as cmdb_resources
from bingops.api.v1.cmdb import sync_tasks as cmdb_sync_tasks
from bingops.api.v1.cmdb import tags as cmdb_tags
from bingops.core.config import settings
from bingops.core.exceptions import (
    AuthenticationError,
    BingOpsError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from bingops.core.logging import setup_logging

logger = logging.getLogger(__name__)

# 启动时统一配置日志（stdout 输出，供 K8S 采集）
setup_logging()

# ── 数据库引擎 ─────────────────────────────────────────────────────────────────
engine = create_async_engine(settings.database_url, echo=settings.debug, pool_size=10)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """注入数据库会话。"""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


# ── 应用生命周期 ───────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """启动/关闭钩子。"""
    # 启动时注入数据库会话依赖
    app.dependency_overrides[dependencies.get_db_session] = get_async_session

    # 启动 Kafka 消费者（仅在 kafka_enabled=true 时）
    if settings.kafka_enabled:
        from bingops.tasks.cmdb.startup import start_cmdb_kafka_consumer
        await start_cmdb_kafka_consumer(async_session_factory)

    yield

    # 关闭时停止 Kafka 消费者
    if settings.kafka_enabled:
        from bingops.tasks.cmdb.startup import stop_cmdb_kafka_consumer
        await stop_cmdb_kafka_consumer()

    # 关闭时释放连接池
    await engine.dispose()


# ── FastAPI 实例 ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="BingOps",
    description="运维平台后端 API",
    version="0.1.0",
    lifespan=lifespan,
)

# 请求链路日志（request_id 分配 + 出入口记录 + 未捕获异常堆栈）
app.add_middleware(RequestLoggingMiddleware)

# Swagger UI 中显示 Bearer Token 认证按钮
security = HTTPBearer(auto_error=False)


# ── 异常处理器 ─────────────────────────────────────────────────────────────────
@app.exception_handler(BingOpsError)
async def bingops_error_handler(request: Request, exc: BingOpsError) -> JSONResponse:
    status_map: dict[type[BingOpsError], int] = {
        ValidationError: 400,
        AuthenticationError: 401,
        PermissionDeniedError: 403,
        NotFoundError: 404,
        ConflictError: 409,
    }
    status_code = status_map.get(type(exc), 500)
    logger.warning(
        "Business exception handled",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": status_code,
            "error_message": exc.message,
        },
    )
    return JSONResponse(
        status_code=status_code,
        content={"code": status_code, "message": exc.message, "data": None},
    )


# ── 注册路由 ───────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(roles.router)
app.include_router(cmdb_models.router)
app.include_router(cmdb_resources.router)
app.include_router(cmdb_relationships.router)
app.include_router(cmdb_tags.router)
app.include_router(cmdb_apps.router)
app.include_router(cmdb_changes.router)
app.include_router(cmdb_sync_tasks.router)
app.include_router(tickets.router)


# ── 健康检查 ───────────────────────────────────────────────────────────────────
@app.get("/api/health", tags=["system"])
async def health_check() -> dict:
    return {"code": 0, "message": "ok", "data": {"status": "healthy"}}
