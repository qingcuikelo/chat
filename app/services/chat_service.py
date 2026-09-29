"""聊天业务逻辑：保存消息、构造上下文、流式调用 LLM。"""

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.redis import get_redis
from app.models.conversation import Conversation
from app.models.message import Message
from app.repositories.message_repository import MessageRepository
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数（无第三方分词器时的启发式）。"""
    return max(1, len(text) // 4) if text else 0


class ChatService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.messages = MessageRepository(db)
        self.llm = LLMService()

    async def _set_request_status(self, request_id: str, status: str) -> None:
        redis = get_redis()
        await redis.set(
            f"chat:request:{request_id}", status, ex=settings.REDIS_REQUEST_TTL
        )

    def _build_prompt(self, history: list[Message]) -> list[dict[str, str]]:
        prompt: list[dict[str, str]] = [
            {"role": "system", "content": settings.LLM_SYSTEM_PROMPT}
        ]
        for message in history:
            if message.role in ("system", "user", "assistant"):
                prompt.append({"role": message.role, "content": message.content})
        return prompt

    async def stream_chat(
        self, *, conversation: Conversation, user_message: str
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        request_id = f"req_{uuid.uuid4().hex[:12]}"
        await self._set_request_status(request_id, "pending")

        user_msg = await self.messages.create(
            conversation_id=conversation.id,
            role="user",
            content=user_message,
            token_count=estimate_tokens(user_message),
        )
        await self.db.commit()

        yield "start", {"request_id": request_id, "user_message_id": user_msg.id}

        try:
            history, _ = await self.messages.list_by_conversation(
                conversation.id,
                page=1,
                page_size=settings.CHAT_CONTEXT_MAX_MESSAGES,
                order="asc",
            )
            prompt = self._build_prompt(history)

            await self._set_request_status(request_id, "streaming")
            parts: list[str] = []
            async for token in self.llm.stream_chat(prompt):
                parts.append(token)
                yield "delta", {"content": token}

            content = "".join(parts)
            assistant_msg = await self.messages.create(
                conversation_id=conversation.id,
                role="assistant",
                content=content,
                token_count=estimate_tokens(content),
            )
            await self.db.commit()
            await self._set_request_status(request_id, "completed")

            yield "done", {
                "assistant_message_id": assistant_msg.id,
                "token_count": assistant_msg.token_count,
                "finish_reason": "stop",
            }
        except Exception:
            logger.exception("聊天处理失败 request_id=%s", request_id)
            await self.db.rollback()
            await self._set_request_status(request_id, "failed")
            raise
