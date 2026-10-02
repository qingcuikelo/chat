"""日志初始化（API 与 worker 共用）。

- 输出到 stdout（容器友好），可选带轮转的文件输出；
- `text` / `json` 两种格式；
- 自动注入 `request_id` / `user_id` / `conversation_id`；
- 接管 uvicorn 日志并降低第三方库噪音。
"""

import json
import logging
import sys
from logging.handlers import RotatingFileHandler

from app.core.config import settings
from app.core.context import (
    conversation_id_var,
    request_id_var,
    user_id_var,
)

# 降噪：这些库只在 WARNING 及以上输出
_NOISY_LOGGERS = ("httpx", "httpcore", "httpx2", "httpcore2", "aiomysql", "watchfiles")

# 接管以纳入统一格式
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


class RequestContextFilter(logging.Filter):
    """把请求上下文注入日志记录（缺失时使用默认值）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get()
        if not hasattr(record, "user_id"):
            record.user_id = user_id_var.get()
        if not hasattr(record, "conversation_id"):
            record.conversation_id = conversation_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "user_id": getattr(record, "user_id", "-"),
            "conversation_id": getattr(record, "conversation_id", "-"),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _build_formatter() -> logging.Formatter:
    if settings.LOG_FORMAT.lower() == "json":
        return JsonFormatter()
    fmt = (
        "%(asctime)s | %(levelname)-8s | "
        "rid=%(request_id)s uid=%(user_id)s cid=%(conversation_id)s | "
        "%(name)s | %(message)s"
    )
    return logging.Formatter(fmt)


def _configure_handlers() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    root.setLevel(level)

    formatter = _build_formatter()
    context_filter = RequestContextFilter()

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    stdout_handler.addFilter(context_filter)
    root.addHandler(stdout_handler)

    if settings.LOG_FILE:
        file_handler = RotatingFileHandler(
            settings.LOG_FILE,
            maxBytes=settings.LOG_FILE_MAX_BYTES,
            backupCount=settings.LOG_FILE_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(context_filter)
        root.addHandler(file_handler)


def _reduce_noise() -> None:
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def _takeover_uvicorn_loggers() -> None:
    """移除 uvicorn 自带 handler，使其日志走 root（从而应用统一格式）。"""
    for name in _UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True


def setup_logging() -> None:
    _configure_handlers()
    _reduce_noise()
    _takeover_uvicorn_loggers()
