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
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository
from app.services.cache_service import CacheService
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数（无第三方分词器时的启发式）。"""
    return max(1, len(text) // 4) if text else 0


class ChatService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.messages = MessageRepository(db)
        self.conversations = ConversationRepository(db)
        self.llm = LLMService()
        self.cache = CacheService()

    async def _set_request_status(self, request_id: str, status: str) -> None:
        redis = get_redis()
        await redis.set(
            f"chat:request:{request_id}", status, ex=settings.REDIS_REQUEST_TTL
        )

    @staticmethod
    def _build_prompt(history: list[dict[str, Any]]) -> list[dict[str, str]]:
        prompt: list[dict[str, str]] = [
            {"role": "system", "content": settings.LLM_SYSTEM_PROMPT}
        ]
        for message in history:
            role = message.get("role")
            content = message.get("content")
            if role in ("system", "user", "assistant") and content:
                prompt.append({"role": role, "content": content})
        return prompt

    @staticmethod
    def _to_session_messages(messages: list[Message]) -> list[dict[str, Any]]:
        return [{"role": m.role, "content": m.content} for m in messages]

    async def _ensure_session(self, conversation_id: int) -> None:
        """会话缓存未命中时，从数据库回填最近的历史消息。"""
        if await self.cache.get_session_messages(conversation_id):
            return
        recent = await self.messages.list_recent(
            conversation_id, settings.CHAT_CONTEXT_MAX_MESSAGES
        )
        await self.cache.set_session_messages(
            conversation_id, self._to_session_messages(recent)
        )

    async def _discard_failed_turn(
        self, conversation_id: int, user_message_id: int
    ) -> None:
        """LLM 失败时删除本轮 user 消息并重建会话缓存，避免留下孤儿消息。"""
        try:
            message = await self.messages.get(user_message_id)
            if message is not None:
                await self.messages.delete(message)
                await self.db.commit()
            recent = await self.messages.list_recent(
                conversation_id, settings.CHAT_CONTEXT_MAX_MESSAGES
            )
            await self.cache.set_session_messages(
                conversation_id, self._to_session_messages(recent)
            )
        except Exception:
            logger.warning(
                "清理失败轮次消息时出错 conversation_id=%s", conversation_id, exc_info=True
            )

    async def stream_chat(
        self, *, conversation: Conversation, user_message: str
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        request_id = f"req_{uuid.uuid4().hex[:12]}"
        await self._set_request_status(request_id, "pending")

        await self._ensure_session(conversation.id)

        user_msg = await self.messages.create(
            conversation_id=conversation.id,
            role="user",
            content=user_message,
            token_count=estimate_tokens(user_message),
        )
        user_message_id = user_msg.id
        await self.conversations.touch(conversation.id)
        await self.db.commit()
        await self.cache.push_session_message(
            conversation.id, {"role": "user", "content": user_message}
        )
        await self.cache.invalidate_conversation(conversation.id)

        yield "start", {"request_id": request_id, "user_message_id": user_message_id}

        try:
            session_messages = await self.cache.get_session_messages(conversation.id)
            prompt = self._build_prompt(session_messages)

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
            await self.cache.push_session_message(
                conversation.id, {"role": "assistant", "content": content}
            )
            await self._set_request_status(request_id, "completed")

            yield "done", {
                "assistant_message_id": assistant_msg.id,
                "token_count": assistant_msg.token_count,
                "finish_reason": "stop",
            }
        except Exception:
            logger.exception("聊天处理失败 request_id=%s", request_id)
            await self.db.rollback()
            await self._discard_failed_turn(conversation.id, user_message_id)
            await self._set_request_status(request_id, "failed")
            raise
