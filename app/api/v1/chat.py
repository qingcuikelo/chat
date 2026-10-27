"""聊天路由（SSE 流式 + Last-Event-ID 断点续传）。"""

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sse_starlette.sse import EventSourceResponse

from app.core import lifecycle
from app.core.concurrency import acquire_chat_slot, release_chat_slot
from app.core.config import settings
from app.core.context import conversation_id_var
from app.core.database import get_db, get_session_factory
from app.core.exceptions import (
    AppError,
    RateLimitedError,
    ServerShuttingDownError,
    StreamNotFoundError,
)
from app.core.openapi import error_response, sse_response
from app.core.rate_limit import check_rate_limit
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.chat import ChatRequest
from app.services.chat_service import ChatService
from app.services.conversation_service import ConversationService
from app.services.stream_service import StreamService
from app.services.usage_service import UsageService

logger = logging.getLogger(__name__)


class ChatEventSourceResponse(EventSourceResponse):
    """为 OpenAPI 声明 text/event-stream 响应类型。"""

    media_type = "text/event-stream"


router = APIRouter(tags=["chat"])

_CHAT_429_HEADERS = {
    "Retry-After": {
        "description": "限流或配额恢复前的建议等待秒数。",
        "schema": {"type": "integer"},
    },
    "X-Quota-Limit": {
        "description": "每日 token 配额；仅配额错误返回。",
        "schema": {"type": "integer"},
    },
    "X-Quota-Used": {
        "description": "当日已用 token；仅配额错误返回。",
        "schema": {"type": "integer"},
    },
    "X-Quota-Remaining": {
        "description": "剩余 token；仅配额错误返回。",
        "schema": {"type": "integer"},
    },
}


async def _sse_generator(
    service: ChatService,
    request_id: str,
    last_id: str | None,
    stream: StreamService,
) -> AsyncIterator[dict[str, Any]]:
    await stream.reader_join(request_id)
    try:
        async for event, data, event_id in service.iter_stream(request_id, last_id=last_id):
            payload: dict[str, Any] = {
                "event": event,
                "data": json.dumps(data, ensure_ascii=False),
            }
            if event_id:
                payload["id"] = event_id
            yield payload
    except AppError as exc:
        yield {
            "event": "error",
            "data": json.dumps({"code": exc.code, "message": exc.message}, ensure_ascii=False),
        }
    except Exception:
        logger.exception("聊天流消费异常 request_id=%s", request_id)
        yield {
            "event": "error",
            "data": json.dumps(
                {"code": "INTERNAL_ERROR", "message": "服务器内部错误"},
                ensure_ascii=False,
            ),
        }
    finally:
        await stream.reader_leave(request_id)


@router.post(
    "/chat",
    response_class=ChatEventSourceResponse,
    summary="发送消息并建立 SSE 流",
    description=(
        "返回 `text/event-stream`。事件顺序为 `start → delta* → done`；"
        "生成中的异常以 `error` 终止。建立流前的配额、限流或会话错误仍返回 JSON 错误响应。"
    ),
    responses={
        200: sse_response("SSE 事件流：start → delta* → done，异常为 error。"),
        400: error_response("请求体校验失败。"),
        401: error_response("未认证或访问令牌失效。"),
        404: error_response("会话不存在或不属于当前用户。"),
        429: error_response("触发请求限流或每日 token 配额。", headers=_CHAT_429_HEADERS),
        500: error_response("服务器内部错误。"),
        503: error_response("服务正在停机。"),
    },
)
async def chat(
    payload: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> EventSourceResponse:
    if lifecycle.is_shutting_down():
        raise ServerShuttingDownError()

    await UsageService(db).ensure_within_quota(current_user.id)
    await check_rate_limit(f"rate_limit:user:{current_user.id}", settings.RATE_LIMIT_PER_MINUTE)
    conversation = await ConversationService(db).get(payload.conversation_id, current_user.id)
    conversation_id = conversation.id
    conversation_id_var.set(str(conversation_id))
    # 结束请求级事务，释放连接：流式期间改用独立短生命周期会话
    await db.rollback()

    if not await acquire_chat_slot(current_user.id):
        raise RateLimitedError("同时进行的聊天过多，请稍后重试", headers={"Retry-After": "5"})

    request_id = f"req_{uuid.uuid4().hex[:12]}"
    stream = StreamService()
    try:
        await stream.init(request_id, current_user.id, conversation_id)
        service = ChatService(session_factory=session_factory)
        try:
            lifecycle.spawn_generation(
                service.run_generation(
                    request_id=request_id,
                    conversation_id=conversation_id,
                    user_id=current_user.id,
                    user_message=payload.message,
                ),
                name=f"chat:{request_id}",
            )
        except RuntimeError as exc:
            raise ServerShuttingDownError() from exc
    except BaseException:
        await release_chat_slot(current_user.id)
        raise

    return EventSourceResponse(
        _sse_generator(service, request_id, None, stream),
        ping=settings.CHAT_STREAM_HEARTBEAT,
        headers={"Cache-Control": "no-cache"},
    )


@router.get(
    "/chat/stream/{request_id}",
    response_class=ChatEventSourceResponse,
    summary="断点续传 SSE 流",
    description=(
        "按 `Last-Event-ID`（优先）或 `last_event_id` 查询参数，从指定 Redis Stream "
        "事件 ID 之后继续推送；已完成或失败的流会重放剩余事件后结束。"
    ),
    responses={
        200: sse_response("续传的 SSE 事件流。"),
        401: error_response("未认证或访问令牌失效。"),
        404: error_response("流不存在、已过期或不属于当前用户。"),
        500: error_response("服务器内部错误。"),
    },
)
async def resume_stream(
    request_id: str,
    last_event_id: str | None = Header(
        None, alias="Last-Event-ID", description="最后收到的 SSE 事件 ID；优先于查询参数。"
    ),
    last_event_id_query: str | None = Query(
        None, alias="last_event_id", description="Last-Event-ID 的查询参数回退。"
    ),
    current_user: User = Depends(get_current_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> EventSourceResponse:
    """按 Last-Event-ID 续接指定请求的剩余 SSE 事件。"""
    stream = StreamService()
    meta = await stream.get_meta(request_id)
    if not meta or str(meta.get("user_id")) != str(current_user.id):
        raise StreamNotFoundError()

    last_id = last_event_id or last_event_id_query
    service = ChatService(session_factory=session_factory)
    return EventSourceResponse(
        _sse_generator(service, request_id, last_id, stream),
        ping=settings.CHAT_STREAM_HEARTBEAT,
        headers={"Cache-Control": "no-cache"},
    )
