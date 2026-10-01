"""日志初始化。"""

import logging

from app.core.config import settings
from app.core.context import request_id_var


class RequestIDFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def setup_logging() -> None:
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    if settings.LOG_FORMAT.lower() == "json":
        fmt = (
            '{"time":"%(asctime)s","level":"%(levelname)s",'
            '"request_id":"%(request_id)s","logger":"%(name)s","message":"%(message)s"}'
        )
    else:
        fmt = "%(asctime)s | %(levelname)-8s | %(request_id)s | %(name)s | %(message)s"
    logging.basicConfig(level=level, format=fmt, force=True)

    request_id_filter = RequestIDFilter()
    for handler in logging.getLogger().handlers:
        handler.addFilter(request_id_filter)
