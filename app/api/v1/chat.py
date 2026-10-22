"""聊天路由（SSE 流式 + Last-Event-ID 断点续传）。"""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sse_starlette.sse import EventSourceResponse

from app.core.config import settings
from app.core.context import conversation_id_var
from app.core.database import get_db, get_session_factory
from app.core.exceptions import AppError, StreamNotFoundError
from app.core.rate_limit import check_rate_limit
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.chat import ChatRequest
from app.services.chat_service import ChatService
from app.services.conversation_service import ConversationService
from app.services.stream_service import StreamService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

# 持有后台生成任务的强引用，避免被 GC；不随请求连接取消
_background_tasks: set[asyncio.Task] = set()


def _spawn_generation(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


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


@router.post("/chat")
async def chat(
    payload: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> EventSourceResponse:
    await check_rate_limit(f"rate_limit:user:{current_user.id}", settings.RATE_LIMIT_PER_MINUTE)
    conversation = await ConversationService(db).get(payload.conversation_id, current_user.id)
    conversation_id = conversation.id
    conversation_id_var.set(str(conversation_id))
    # 结束请求级事务，释放连接：流式期间改用独立短生命周期会话
    await db.rollback()

    request_id = f"req_{uuid.uuid4().hex[:12]}"
    stream = StreamService()
    await stream.init(request_id, current_user.id, conversation_id)

    service = ChatService(session_factory=session_factory)
    _spawn_generation(
        service.run_generation(
            request_id=request_id,
            conversation_id=conversation_id,
            user_id=current_user.id,
            user_message=payload.message,
        )
    )

    return EventSourceResponse(
        _sse_generator(service, request_id, None, stream),
        ping=settings.CHAT_STREAM_HEARTBEAT,
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/chat/stream/{request_id}")
async def resume_stream(
    request_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> EventSourceResponse:
    """从 `Last-Event-ID` 继续消费指定聊天流（断点续传）。"""
    stream = StreamService()
    meta = await stream.get_meta(request_id)
    if not meta or str(meta.get("user_id")) != str(current_user.id):
        raise StreamNotFoundError()

    last_id = request.headers.get("Last-Event-ID") or request.query_params.get("last_event_id")
    service = ChatService(session_factory=session_factory)
    return EventSourceResponse(
        _sse_generator(service, request_id, last_id, stream),
        ping=settings.CHAT_STREAM_HEARTBEAT,
        headers={"Cache-Control": "no-cache"},
    )
