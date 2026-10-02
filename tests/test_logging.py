"""日志模块测试。"""

import json
import logging

from app.core import logging as app_logging
from app.core.context import conversation_id_var, request_id_var, user_id_var


def _record() -> logging.LogRecord:
    return logging.LogRecord("t", logging.INFO, "p", 1, "msg", None, None)


def test_context_filter_injects_defaults():
    record = _record()
    assert app_logging.RequestContextFilter().filter(record) is True
    assert record.request_id == "-"
    assert record.user_id == "-"
    assert record.conversation_id == "-"


def test_context_filter_uses_contextvars():
    record = _record()
    request_id_var.set("r1")
    user_id_var.set("u1")
    conversation_id_var.set("c1")
    try:
        app_logging.RequestContextFilter().filter(record)
        assert (record.request_id, record.user_id, record.conversation_id) == (
            "r1",
            "u1",
            "c1",
        )
    finally:
        request_id_var.set("-")
        user_id_var.set("-")
        conversation_id_var.set("-")


def test_json_formatter_outputs_structured_fields():
    record = _record()
    record.request_id = "r"
    record.user_id = "u"
    record.conversation_id = "c"
    payload = json.loads(app_logging.JsonFormatter().format(record))
    assert payload["message"] == "msg"
    assert payload["level"] == "INFO"
    assert payload["request_id"] == "r"
    assert payload["user_id"] == "u"
    assert payload["conversation_id"] == "c"


def test_setup_logging_takes_over_uvicorn_and_reduces_noise():
    app_logging.setup_logging()

    uvicorn_error = logging.getLogger("uvicorn.error")
    assert uvicorn_error.propagate is True
    assert uvicorn_error.handlers == []

    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger().handlers
