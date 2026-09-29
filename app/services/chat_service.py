"""聊天业务逻辑：保存消息、构造上下文、流式调用 LLM。"""

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.redis import get_redis
from app.core.tokens import count_tokens
from app.models.conversation import Conversation
from app.models.message import Message
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository
from app.services.cache_service import CacheService
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)

TITLE_MAX_LENGTH = 30

SUMMARY_SYSTEM_PROMPT = (
    "你是对话摘要助手。请将给定的对话内容压缩为简洁的中文摘要，"
    "保留关键事实、用户偏好、重要决定与未解决的问题；"
    "不要遗漏关键信息，不要添加评论或解释。"
)


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
    def _build_prompt(
        history: list[dict[str, Any]], summary: str | None = None
    ) -> list[dict[str, str]]:
        """装配 Prompt：system（基础 + 摘要）+ 由近及远纳入的历史消息。"""
        system_messages: list[dict[str, str]] = [
            {"role": "system", "content": settings.LLM_SYSTEM_PROMPT}
        ]
        budget = settings.CHAT_CONTEXT_MAX_TOKENS
        used = count_tokens(system_messages[0]["content"])

        if summary:
            block = f"以下是更早对话的摘要，供你参考：\n{summary}"
            system_messages.append({"role": "system", "content": block})
            used += count_tokens(block)

        selected: list[dict[str, str]] = []
        for message in reversed(history):
            role = message.get("role")
            content = message.get("content")
            if role not in ("system", "user", "assistant") or not content:
                continue
            cost = count_tokens(content)
            if selected and used + cost > budget:
                break
            selected.append({"role": role, "content": content})
            used += cost

        selected.reverse()
        return [*system_messages, *selected]

    @staticmethod
    def _generate_title(text: str) -> str:
        title = " ".join(text.split())
        return title[:TITLE_MAX_LENGTH]

    @staticmethod
    def _to_session_messages(messages: list[Message]) -> list[dict[str, Any]]:
        return [{"id": m.id, "role": m.role, "content": m.content} for m in messages]

    @staticmethod
    def _summary_messages(
        previous: str | None, messages: list[Message]
    ) -> list[dict[str, str]]:
        parts: list[str] = []
        if previous:
            parts.append(f"已有摘要：\n{previous}")
        parts.append("待压缩的对话：")
        parts.extend(f"{m.role}: {m.content}" for m in messages)
        return [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(parts)},
        ]

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

    async def _maybe_compress_context(
        self, conversation_id: int, summary: str | None, watermark: int
    ) -> tuple[str | None, int]:
        """用量接近预算时，把较早消息滚动压缩为摘要，返回 (摘要, 新水位线)。"""
        if not settings.CHAT_SUMMARY_ENABLED:
            return summary, watermark
        try:
            used = (
                count_tokens(settings.LLM_SYSTEM_PROMPT)
                + count_tokens(summary or "")
                + await self.messages.sum_tokens_after(conversation_id, watermark)
            )
            threshold = (
                settings.CHAT_CONTEXT_MAX_TOKENS * settings.CHAT_SUMMARY_TRIGGER_RATIO
            )
            if used <= threshold:
                return summary, watermark

            messages = await self.messages.list_after(conversation_id, watermark)
            keep = settings.CHAT_SUMMARY_KEEP_RECENT_MESSAGES
            if len(messages) <= keep:
                return summary, watermark
            to_summarize = messages[:-keep]

            new_summary = await self.llm.complete(
                self._summary_messages(summary, to_summarize),
                model=settings.LLM_SUMMARY_MODEL or None,
                max_tokens=settings.CHAT_SUMMARY_MAX_TOKENS,
            )
            if not new_summary:
                return summary, watermark

            new_watermark = to_summarize[-1].id
            await self.conversations.update_summary(
                conversation_id, new_summary, new_watermark
            )
            await self.db.commit()
            await self.cache.invalidate_conversation(conversation_id)
            return new_summary, new_watermark
        except Exception:
            logger.warning(
                "生成会话摘要失败 conversation_id=%s", conversation_id, exc_info=True
            )
            await self.db.rollback()
            return summary, watermark

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
        conversation_id = conversation.id
        request_id = f"req_{uuid.uuid4().hex[:12]}"
        await self._set_request_status(request_id, "pending")

        await self._ensure_session(conversation_id)

        user_msg = await self.messages.create(
            conversation_id=conversation_id,
            role="user",
            content=user_message,
            token_count=count_tokens(user_message),
        )
        user_message_id = user_msg.id
        await self.conversations.touch(conversation_id)

        # 首条消息自动生成标题
        if not conversation.title:
            title = self._generate_title(user_message)
            if title:
                conversation.title = title

        await self.db.commit()
        await self.cache.push_session_message(
            conversation_id,
            {"id": user_message_id, "role": "user", "content": user_message},
        )
        await self.cache.invalidate_conversation(conversation_id)

        yield "start", {"request_id": request_id, "user_message_id": user_message_id}

        try:
            summary, watermark = await self._maybe_compress_context(
                conversation_id,
                conversation.summary,
                conversation.summary_until_message_id or 0,
            )

            session_messages = await self.cache.get_session_messages(conversation_id)
            recent = [
                message
                for message in session_messages
                if int(message.get("id") or 0) > watermark
            ]
            prompt = self._build_prompt(recent, summary)

            await self._set_request_status(request_id, "streaming")
            parts: list[str] = []
            async for token in self.llm.stream_chat(prompt):
                parts.append(token)
                yield "delta", {"content": token}

            content = "".join(parts)
            assistant_msg = await self.messages.create(
                conversation_id=conversation_id,
                role="assistant",
                content=content,
                token_count=count_tokens(content),
            )
            await self.db.commit()
            await self.cache.push_session_message(
                conversation_id,
                {"id": assistant_msg.id, "role": "assistant", "content": content},
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
            await self._discard_failed_turn(conversation_id, user_message_id)
            await self._set_request_status(request_id, "failed")
            raise
