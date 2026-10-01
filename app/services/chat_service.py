"""聊天业务逻辑：保存消息、构造上下文、流式调用 LLM。"""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import anyio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.redis import get_redis
from app.core.tokens import count_tokens
from app.models.message import Message
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository
from app.services.cache_service import CacheService
from app.services.llm_service import LLMService, StreamChunk

logger = logging.getLogger(__name__)

TITLE_MAX_LENGTH = 30

EXCLUDED_STATUSES = ("failed", "interrupted")

SUMMARY_SYSTEM_PROMPT = (
    "你是对话摘要助手。请将给定的对话内容压缩为简洁的中文摘要，"
    "保留关键事实、用户偏好、重要决定与未解决的问题；"
    "不要遗漏关键信息，不要添加评论或解释。"
)


class ChatService:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession] | None = None
    ) -> None:
        # 流式路径与请求 session 解耦，使用独立短生命周期会话
        self.session_factory = session_factory or AsyncSessionLocal
        self.llm = LLMService()
        self.cache = CacheService()

    async def _set_request_status(self, request_id: str, status: str) -> None:
        redis = get_redis()
        await redis.set(
            f"chat:request:{request_id}", status, ex=settings.REDIS_REQUEST_TTL
        )

    # ---- 纯函数 ----
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
    def _meta(message: Message) -> dict[str, Any]:
        return message.metadata_ or {}

    @classmethod
    def _to_session_messages(cls, messages: list[Message]) -> list[dict[str, Any]]:
        return [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "status": cls._meta(m).get("status"),
                "answer_to": cls._meta(m).get("answer_to"),
            }
            for m in messages
        ]

    @staticmethod
    def _filter_db_messages(messages: list[Message]) -> list[Message]:
        excluded_answer_to = {
            (m.metadata_ or {}).get("answer_to")
            for m in messages
            if m.role == "assistant" and (m.metadata_ or {}).get("status") in EXCLUDED_STATUSES
        }
        result: list[Message] = []
        for m in messages:
            if m.role == "assistant" and (m.metadata_ or {}).get("status") in EXCLUDED_STATUSES:
                continue
            if m.role == "user" and m.id in excluded_answer_to:
                continue
            result.append(m)
        return result

    @staticmethod
    def _filter_context(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        excluded_answer_to = {
            m.get("answer_to")
            for m in messages
            if m.get("role") == "assistant" and m.get("status") in EXCLUDED_STATUSES
        }
        result: list[dict[str, Any]] = []
        for m in messages:
            if m.get("role") == "assistant" and m.get("status") in EXCLUDED_STATUSES:
                continue
            if m.get("role") == "user" and m.get("id") in excluded_answer_to:
                continue
            result.append(m)
        return result

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

    @staticmethod
    def _assistant_token_count(content: str, usage: dict[str, Any] | None) -> int:
        if usage:
            completion = usage.get("completion_tokens")
            if completion:
                return int(completion)
        return count_tokens(content)

    # ---- 会话与缓存 ----
    async def _persist_user_turn(
        self, conversation_id: int, user_message: str, raw_tokens: int
    ) -> int:
        async with self.session_factory() as session:
            user_msg = await MessageRepository(session).create(
                conversation_id=conversation_id,
                role="user",
                content=user_message,
                token_count=raw_tokens,
            )
            user_message_id = user_msg.id
            conversations = ConversationRepository(session)
            await conversations.touch(conversation_id)
            conversation = await conversations.get(conversation_id)
            if conversation is not None and not conversation.title:
                title = self._generate_title(user_message)
                if title:
                    conversation.title = title
            await session.commit()

        await self.cache.push_session_message(
            conversation_id,
            {
                "id": user_message_id,
                "role": "user",
                "content": user_message,
                "status": None,
                "answer_to": None,
            },
        )
        await self.cache.invalidate_conversation(conversation_id)
        return user_message_id

    async def _refresh_session_cache(self, conversation_id: int) -> None:
        async with self.session_factory() as session:
            recent = await MessageRepository(session).list_recent(
                conversation_id, settings.CHAT_CONTEXT_MAX_MESSAGES
            )
            messages = self._to_session_messages(recent)
        await self.cache.set_session_messages(conversation_id, messages)

    async def _ensure_session(self, conversation_id: int) -> None:
        if await self.cache.get_session_messages(conversation_id):
            return
        await self._refresh_session_cache(conversation_id)

    async def _maybe_compress_context(
        self, conversation_id: int
    ) -> tuple[str | None, int]:
        """用量接近预算时，把较早消息滚动压缩为摘要，返回 (摘要, 水位线)。"""
        async with self.session_factory() as session:
            conversations = ConversationRepository(session)
            messages_repo = MessageRepository(session)
            conversation = await conversations.get(conversation_id)
            if conversation is None:
                return None, 0
            summary = conversation.summary
            watermark = conversation.summary_until_message_id or 0

            if not settings.CHAT_SUMMARY_ENABLED:
                return summary, watermark

            try:
                used = (
                    count_tokens(settings.LLM_SYSTEM_PROMPT)
                    + count_tokens(summary or "")
                    + await messages_repo.sum_tokens_after(conversation_id, watermark)
                )
                threshold = (
                    settings.CHAT_CONTEXT_MAX_TOKENS
                    * settings.CHAT_SUMMARY_TRIGGER_RATIO
                )
                if used <= threshold:
                    return summary, watermark

                history = self._filter_db_messages(
                    await messages_repo.list_after(conversation_id, watermark)
                )
                keep = settings.CHAT_SUMMARY_KEEP_RECENT_MESSAGES
                if len(history) <= keep:
                    return summary, watermark
                to_summarize = history[:-keep]

                new_summary = await self.llm.complete(
                    self._summary_messages(summary, to_summarize),
                    model=settings.LLM_SUMMARY_MODEL or None,
                    max_tokens=settings.CHAT_SUMMARY_MAX_TOKENS,
                )
                if not new_summary:
                    return summary, watermark

                new_watermark = to_summarize[-1].id
                await conversations.update_summary(
                    conversation_id, new_summary, new_watermark
                )
                await session.commit()
            except Exception:
                logger.warning(
                    "生成会话摘要失败 conversation_id=%s", conversation_id, exc_info=True
                )
                await session.rollback()
                return summary, watermark

        await self.cache.invalidate_conversation(conversation_id)
        return new_summary, new_watermark

    async def _persist_assistant_turn(
        self, conversation_id: int, content: str, usage: dict[str, Any] | None
    ) -> tuple[int, int]:
        token_count = self._assistant_token_count(content, usage)
        async with self.session_factory() as session:
            assistant_msg = await MessageRepository(session).create(
                conversation_id=conversation_id,
                role="assistant",
                content=content,
                token_count=token_count,
            )
            assistant_id = assistant_msg.id
            await session.commit()

        await self.cache.push_session_message(
            conversation_id,
            {
                "id": assistant_id,
                "role": "assistant",
                "content": content,
                "status": None,
                "answer_to": None,
            },
        )
        return assistant_id, token_count

    async def _persist_final_turn(
        self,
        *,
        request_id: str,
        conversation_id: int,
        user_message_id: int,
        user_raw_tokens: int,
        assistant_content: str,
        status: str,
        error_code: str | None,
        already_persisted: bool,
    ) -> None:
        """失败/断线收尾：写 assistant 占位并整轮移出上下文（幂等、免疫取消）。"""
        if already_persisted:
            return
        with anyio.CancelScope(shield=True):
            try:
                async with self.session_factory() as session:
                    messages_repo = MessageRepository(session)
                    await messages_repo.create(
                        conversation_id=conversation_id,
                        role="assistant",
                        content=assistant_content,
                        token_count=0,
                        metadata={
                            "status": status,
                            "error_code": error_code,
                            "answer_to": user_message_id,
                        },
                    )
                    await messages_repo.exclude_from_context(
                        user_message_id, user_raw_tokens
                    )
                    await session.commit()
                await self._refresh_session_cache(conversation_id)
                await self.cache.invalidate_conversation(conversation_id)
                await self._set_request_status(request_id, status)
            except Exception:
                logger.warning(
                    "收尾落库失败 conversation_id=%s", conversation_id, exc_info=True
                )

    # ---- 主流程 ----
    async def stream_chat(
        self, *, conversation_id: int, user_message: str
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        request_id = f"req_{uuid.uuid4().hex[:12]}"
        await self._set_request_status(request_id, "pending")

        user_raw_tokens = count_tokens(user_message)
        user_message_id = await self._persist_user_turn(
            conversation_id, user_message, user_raw_tokens
        )

        yield "start", {"request_id": request_id, "user_message_id": user_message_id}

        assistant_parts: list[str] = []
        final_finish = "stop"
        final_usage: dict[str, Any] | None = None
        persisted = False

        try:
            await self._ensure_session(conversation_id)
            summary, watermark = await self._maybe_compress_context(conversation_id)

            session_messages = await self.cache.get_session_messages(conversation_id)
            recent = [
                message
                for message in self._filter_context(session_messages)
                if int(message.get("id") or 0) > watermark
            ]
            prompt = self._build_prompt(recent, summary)

            await self._set_request_status(request_id, "streaming")

            buffer: list[str] = []
            buffered_chars = 0
            last_flush = time.monotonic()
            async for chunk in self.llm.stream_chat(prompt):
                if chunk.kind == "delta" and chunk.text:
                    assistant_parts.append(chunk.text)
                    buffer.append(chunk.text)
                    buffered_chars += len(chunk.text)
                    now = time.monotonic()
                    elapsed_ms = (now - last_flush) * 1000
                    if (
                        buffered_chars >= settings.SSE_BATCH_MIN_CHARS
                        or elapsed_ms >= settings.SSE_BATCH_MAX_INTERVAL_MS
                    ):
                        yield "delta", {"content": "".join(buffer)}
                        buffer.clear()
                        buffered_chars = 0
                        last_flush = now
                elif chunk.kind == "final":
                    final_finish = chunk.finish_reason or "stop"
                    final_usage = chunk.usage

            if buffer:
                yield "delta", {"content": "".join(buffer)}

            content = "".join(assistant_parts)
            assistant_id, token_count = await self._persist_assistant_turn(
                conversation_id, content, final_usage
            )
            persisted = True
            await self._set_request_status(request_id, "completed")

            yield "done", {
                "assistant_message_id": assistant_id,
                "token_count": token_count,
                "finish_reason": final_finish,
            }
        except (asyncio.CancelledError, GeneratorExit):
            logger.info("客户端断开，收尾中断轮次 conversation_id=%s", conversation_id)
            await self._persist_final_turn(
                request_id=request_id,
                conversation_id=conversation_id,
                user_message_id=user_message_id,
                user_raw_tokens=user_raw_tokens,
                assistant_content="".join(assistant_parts),
                status="interrupted",
                error_code=None,
                already_persisted=persisted,
            )
            raise
        except Exception as exc:
            logger.exception("聊天处理失败 request_id=%s", request_id)
            await self._persist_final_turn(
                request_id=request_id,
                conversation_id=conversation_id,
                user_message_id=user_message_id,
                user_raw_tokens=user_raw_tokens,
                assistant_content="".join(assistant_parts),
                status="failed",
                error_code=getattr(exc, "code", "INTERNAL_ERROR"),
                already_persisted=persisted,
            )
            raise
