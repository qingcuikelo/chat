"""OpenAPI 文档辅助定义：SSE 事件契约与统一错误响应。"""

from __future__ import annotations

from typing import Any

API_DESCRIPTION = (
    "AI ChatBot v3 API。业务接口统一使用 `/api/v1` 前缀；"
    "SSE 事件、错误码与版本策略见 `docs/API_CONTRACT.md`。"
)

TAGS_METADATA = [
    {"name": "auth", "description": "注册、登录、令牌刷新/撤销、邮箱验证与密码重置。"},
    {"name": "users", "description": "当前用户资料、用量与配额。"},
    {"name": "conversations", "description": "会话搜索、置顶、归档、消息历史与导出。"},
    {"name": "chat", "description": "SSE 流式聊天与断点续传。"},
    {"name": "system", "description": "健康检查。"},
]


def _event_schema(
    title: str,
    event: str,
    data_schema: dict[str, Any],
    data_example: dict[str, Any],
) -> dict[str, Any]:
    return {
        "title": title,
        "type": "object",
        "required": ["event", "data"],
        "properties": {
            "event": {
                "type": "string",
                "enum": [event],
                "description": "SSE 事件名。",
            },
            "data": data_schema,
            "id": {
                "type": ["string", "null"],
                "description": "Redis Stream 事件 ID；用于 Last-Event-ID 续传，合成错误事件可为空。",
            },
        },
        "examples": [
            {
                "event": event,
                "data": data_example,
                "id": "1710000000000-0",
            }
        ],
    }


SSE_STREAM_SCHEMA: dict[str, Any] = {
    "title": "ChatStreamEvent",
    "description": (
        "SSE 帧信封。`data` 为 JSON 字符串；事件顺序为 start → delta* → "
        "done，异常时为 error（终止）。心跳为 SSE 注释行，不属于事件。"
    ),
    "oneOf": [
        _event_schema(
            "ChatStartStreamEvent",
            "start",
            {
                "type": "object",
                "required": ["request_id", "user_message_id"],
                "properties": {
                    "request_id": {"type": "string", "description": "本次生成的请求 ID。"},
                    "user_message_id": {
                        "type": "integer",
                        "format": "int64",
                        "description": "已落库的用户消息 ID。",
                    },
                },
            },
            {"request_id": "req_0123456789ab", "user_message_id": 41},
        ),
        _event_schema(
            "ChatDeltaStreamEvent",
            "delta",
            {
                "type": "object",
                "required": ["content"],
                "properties": {"content": {"type": "string", "description": "本次增量文本。"}},
            },
            {"content": "你好"},
        ),
        _event_schema(
            "ChatDoneStreamEvent",
            "done",
            {
                "type": "object",
                "required": ["assistant_message_id", "token_count", "finish_reason"],
                "properties": {
                    "assistant_message_id": {"type": "integer", "format": "int64"},
                    "token_count": {"type": "integer", "format": "int64"},
                    "finish_reason": {"type": "string", "description": "停止原因。"},
                },
            },
            {
                "assistant_message_id": 42,
                "token_count": 128,
                "finish_reason": "stop",
            },
        ),
        _event_schema(
            "ChatErrorStreamEvent",
            "error",
            {
                "type": "object",
                "required": ["code", "message"],
                "properties": {
                    "code": {"type": "string", "description": "稳定错误码。"},
                    "message": {"type": "string", "description": "人类可读说明。"},
                },
            },
            {"code": "LLM_ERROR", "message": "模型服务暂时不可用"},
        ),
    ],
}

SSE_EXAMPLE = """id: 1710000000000-0
event: start
data: {"request_id":"req_0123456789ab","user_message_id":41}

id: 1710000000001-0
event: delta
data: {"content":"你好"}

id: 1710000000002-0
event: done
data: {"assistant_message_id":42,"token_count":128,"finish_reason":"stop"}

"""

ERROR_SCHEMA: dict[str, Any] = {
    "title": "ErrorResponse",
    "type": "object",
    "required": ["detail"],
    "properties": {
        "detail": {
            "type": "object",
            "required": ["code", "message"],
            "properties": {
                "code": {"type": "string", "description": "稳定错误码。"},
                "message": {"type": "string", "description": "人类可读说明。"},
                "errors": {
                    "type": "array",
                    "items": {},
                    "description": "仅 400 参数校验错误可能返回。",
                },
            },
        }
    },
}


def sse_response(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {
            "text/event-stream": {
                "schema": SSE_STREAM_SCHEMA,
                "example": SSE_EXAMPLE,
            }
        },
    }


def error_response(description: str, *, headers: dict[str, Any] | None = None) -> dict[str, Any]:
    response: dict[str, Any] = {
        "description": description,
        "content": {"application/json": {"schema": ERROR_SCHEMA}},
    }
    if headers:
        response["headers"] = headers
    return response
