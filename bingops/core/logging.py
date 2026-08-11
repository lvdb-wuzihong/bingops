"""统一日志配置。

应用启动时配置根 logger，双通道输出：
- stdout：始终开启，供 kubectl logs / 控制台排障
- 文件：配置 BINGOPS_LOG_DIR 后开启，按天轮转 + gzip 压缩，供采集器（Filebeat/logtail）读取

开发环境（debug=true）stdout 走人类可读格式；文件与生产 stdout 均为 JSON 单行格式。
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler

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


def _gzip_rotator(source: str, dest: str) -> None:
    """轮转时将旧日志 gzip 压缩到目标文件并删除原件。"""
    with open(source, "rb") as f_in, gzip.open(dest, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    os.remove(source)


def _gzip_namer(name: str) -> str:
    """轮转文件名追加 .gz 后缀。"""
    return f"{name}.gz"


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

    # 文件通道：供采集器落盘采集，按天轮转 + gzip 压缩 + 保留期控制
    if settings.log_dir:
        os.makedirs(settings.log_dir, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            filename=os.path.join(settings.log_dir, "bingops.log"),
            when="midnight",
            backupCount=settings.log_retention_days,
            encoding="utf-8",
        )
        file_handler.setFormatter(JsonFormatter())
        file_handler.namer = _gzip_namer
        file_handler.rotator = _gzip_rotator
        root.addHandler(file_handler)
