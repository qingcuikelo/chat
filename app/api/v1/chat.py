"""聊天路由（SSE 流式）。"""

import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sse_starlette.sse import EventSourceResponse

from app.core.config import settings
from app.core.database import get_db, get_session_factory
from app.core.exceptions import AppError
from app.core.rate_limit import check_rate_limit
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.chat import ChatRequest
from app.services.chat_service import ChatService
from app.services.conversation_service import ConversationService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


@router.post("/chat")
async def chat(
    payload: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> EventSourceResponse:
    await check_rate_limit(current_user.id)
    conversation = await ConversationService(db).get(
        payload.conversation_id, current_user.id
    )
    conversation_id = conversation.id
    # 结束请求级事务，释放连接：流式期间改用独立短生命周期会话
    await db.rollback()

    chat_service = ChatService(session_factory=session_factory)

    async def event_generator() -> AsyncIterator[dict]:
        try:
            async for event, data in chat_service.stream_chat(
                conversation_id=conversation_id, user_message=payload.message
            ):
                yield {
                    "event": event,
                    "data": json.dumps(data, ensure_ascii=False),
                }
        except AppError as exc:
            yield {
                "event": "error",
                "data": json.dumps(
                    {"code": exc.code, "message": exc.message}, ensure_ascii=False
                ),
            }
        except Exception:
            logger.exception("聊天流式处理异常（流中已发送部分增量）")
            yield {
                "event": "error",
                "data": json.dumps(
                    {"code": "INTERNAL_ERROR", "message": "服务器内部错误"},
                    ensure_ascii=False,
                ),
            }

    return EventSourceResponse(
        event_generator(),
        ping=settings.CHAT_STREAM_HEARTBEAT,
        headers={"Cache-Control": "no-cache"},
    )
