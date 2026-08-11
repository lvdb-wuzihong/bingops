"""统一日志配置。

应用启动时配置根 logger：
- 开发环境（debug=true）：人类可读的控制台格式
- 生产环境（debug=false）：JSON 结构化单行格式

所有日志只输出到 stdout，禁止写文件——容器化无状态部署下，
stdout 由 K8S 采集（kubectl logs / 节点侧采集器）。
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

from bingops.core.config import settings

# 请求链路追踪 ID，由请求日志中间件在 HTTP 入口设置
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_CONSOLE_FORMAT = "[%(asctime)s] [%(levelname)-8s] [%(name)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# LogRecord 内置属性，JSON 格式化时用于分离 extra 字段
_RESERVED_ATTRS = set(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)),
)


class JsonFormatter(logging.Formatter):
    """生产环境 JSON 单行格式（字段见日志规范）。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        request_id = request_id_var.get()
        if request_id != "-":
            payload["request_id"] = request_id

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        extra = {
            k: v for k, v in record.__dict__.items()
            if k not in _RESERVED_ATTRS and k != "request_id"
        }
        if extra:
            payload["extra"] = extra

        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging() -> None:
    """配置根 logger。在创建 FastAPI 应用前调用一次。"""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    formatter: logging.Formatter
    if settings.debug:
        formatter = logging.Formatter(_CONSOLE_FORMAT, datefmt=_DATE_FORMAT)
    else:
        formatter = JsonFormatter()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
