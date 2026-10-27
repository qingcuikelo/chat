"""聊天业务逻辑：保存消息、构造上下文、流式调用 LLM。

- v2 WP4e：生成与连接解耦（Redis Stream 缓冲，支持 Last-Event-ID 续传）。
- v2 技术债 B2：消息 ID 由 Redis 分配，一轮仅一次提交（user+assistant 同事务）。
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

import anyio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.concurrency import release_chat_slot
from app.core.config import settings
from app.core.database import AsyncSessionLocal, utcnow
from app.core.redis import get_redis
from app.core.text import generate_title
from app.core.tokens import count_tokens
from app.models.message import Message
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository
from app.services.cache_service import CacheService
from app.services.llm_service import LLMService
from app.services.stream_service import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    TERMINAL_STATUSES,
    StreamService,
)
from app.services.summary_service import SummaryService
from app.services.usage_service import UsageService
from app.services.write_queue import WriteTurn, enqueue_turn
from app.workers.queue import enqueue_summary

logger = logging.getLogger(__name__)

EXCLUDED_STATUSES = ("failed", "interrupted")

MESSAGE_ID_KEY = "chat:message_id"

HEARTBEAT_INTERVAL_SECONDS = 5
STREAM_INIT_WAIT_SECONDS = 10


class _StreamAbandoned(Exception):
    """无订阅者超过宽限期，主动放弃生成。"""


class ChatService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        # 流式路径与请求 session 解耦，使用独立短生命周期会话
        self.session_factory = session_factory or AsyncSessionLocal
        self.llm = LLMService()
        self.cache = CacheService()

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
        return generate_title(text)

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
    def _assistant_token_count(content: str, usage: dict[str, Any] | None) -> int:
        if usage:
            completion = usage.get("completion_tokens")
            if completion:
                return int(completion)
        return count_tokens(content)

    # ---- ID 分配 / 会话缓存 ----
    async def _allocate_message_id(self) -> int:
        """用 Redis 原子分配消息 ID（首次以数据库 MAX(id) 播种）。"""
        redis = get_redis()
        if not await redis.exists(MESSAGE_ID_KEY):
            async with self.session_factory() as session:
                seed = await MessageRepository(session).max_id()
            await redis.set(MESSAGE_ID_KEY, seed, nx=True)
        return int(await redis.incr(MESSAGE_ID_KEY))

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

    async def _maybe_set_title(
        self,
        conversations: ConversationRepository,
        conversation_id: int,
        user_message: str,
    ) -> None:
        conversation = await conversations.get(conversation_id)
        if conversation is not None and not conversation.title:
            title = self._generate_title(user_message)
            if title:
                conversation.title = title

    async def _maybe_compress_context(self, conversation_id: int) -> tuple[str | None, int]:
        """用量接近预算时触发滚动摘要；后台队列优先，失败/关闭时同步回退。"""
        async with self.session_factory() as session:
            service = SummaryService(session)
            summary, watermark = await service.state(conversation_id)

            if not settings.CHAT_SUMMARY_ENABLED:
                return summary, watermark

            if settings.SUMMARY_QUEUE_ENABLED:
                try:
                    if await service.should_compress(conversation_id, summary, watermark):
                        await enqueue_summary(conversation_id)
                except Exception:
                    logger.warning(
                        "投递摘要任务失败，回退同步 conversation_id=%s",
                        conversation_id,
                        exc_info=True,
                    )
                    return await service.compress(conversation_id)
                return summary, watermark

            new_summary, new_watermark = await service.compress(conversation_id)

        await self.cache.invalidate_conversation(conversation_id)
        return new_summary, new_watermark

    # ---- 落库（单轮单次提交）----
    async def _cache_success_turn(
        self,
        conversation_id: int,
        assistant_message_id: int,
        assistant_content: str,
    ) -> None:
        await self.cache.push_session_message(
            conversation_id,
            {
                "id": assistant_message_id,
                "role": "assistant",
                "content": assistant_content,
                "status": None,
                "answer_to": None,
            },
        )
        await self.cache.invalidate_conversation(conversation_id)
        await self.cache.mark_conversation_write(conversation_id)

    async def _persist_success_turn(
        self,
        *,
        request_id: str,
        conversation_id: int,
        user_id: int,
        user_message_id: int,
        assistant_message_id: int,
        user_message: str,
        user_raw_tokens: int,
        assistant_content: str,
        usage: dict[str, Any] | None,
        prompt_tokens: int,
    ) -> tuple[int, int]:
        token_count = self._assistant_token_count(assistant_content, usage)
        usage_date = utcnow().date()

        if settings.WRITE_QUEUE_ENABLED:
            queued = await enqueue_turn(
                WriteTurn(
                    request_id=request_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    user_message_id=user_message_id,
                    assistant_message_id=assistant_message_id,
                    user_content=user_message,
                    assistant_content=assistant_content,
                    user_token_count=user_raw_tokens,
                    assistant_token_count=token_count,
                    status="completed",
                    prompt_tokens=prompt_tokens,
                    completion_tokens=token_count,
                    usage_date=usage_date.isoformat(),
                    created_at=utcnow(),
                )
            )
            if queued:
                await self._cache_success_turn(
                    conversation_id, assistant_message_id, assistant_content
                )
                return assistant_message_id, token_count

        async with self.session_factory() as session:
            messages = MessageRepository(session)
            await messages.create(
                message_id=user_message_id,
                conversation_id=conversation_id,
                role="user",
                content=user_message,
                token_count=user_raw_tokens,
            )
            assistant_msg = await messages.create(
                message_id=assistant_message_id,
                conversation_id=conversation_id,
                role="assistant",
                content=assistant_content,
                token_count=token_count,
            )
            conversations = ConversationRepository(session)
            await conversations.touch(conversation_id)
            await self._maybe_set_title(conversations, conversation_id, user_message)
            await UsageService(session).record_turn(
                user_id=user_id,
                usage_date=usage_date,
                prompt_tokens=prompt_tokens,
                completion_tokens=token_count,
            )
            await session.commit()
            assistant_id = assistant_msg.id

        await self._cache_success_turn(conversation_id, assistant_id, assistant_content)
        await UsageService().add_redis_tokens(user_id, usage_date, prompt_tokens + token_count)
        return assistant_id, token_count

    async def _persist_final_turn(
        self,
        *,
        request_id: str,
        conversation_id: int,
        user_id: int,
        user_message_id: int,
        user_message: str,
        user_raw_tokens: int,
        assistant_content: str,
        status: str,
        error_code: str | None,
        already_persisted: bool,
    ) -> None:
        """失败/中断收尾：一次事务落 user + assistant 占位，整轮移出上下文。"""
        if already_persisted:
            return
        with anyio.CancelScope(shield=True):
            try:
                assistant_message_id = await self._allocate_message_id()

                if settings.WRITE_QUEUE_ENABLED:
                    queued = await enqueue_turn(
                        WriteTurn(
                            request_id=request_id,
                            conversation_id=conversation_id,
                            user_id=user_id,
                            user_message_id=user_message_id,
                            assistant_message_id=assistant_message_id,
                            user_content=user_message,
                            assistant_content=assistant_content,
                            user_token_count=0,
                            assistant_token_count=0,
                            status=status,
                            error_code=error_code,
                            user_metadata={"raw_token_count": user_raw_tokens},
                            assistant_metadata={
                                "status": status,
                                "error_code": error_code,
                                "answer_to": user_message_id,
                            },
                            created_at=utcnow(),
                        )
                    )
                    if queued:
                        await self.cache.push_session_message(
                            conversation_id,
                            {
                                "id": assistant_message_id,
                                "role": "assistant",
                                "content": assistant_content,
                                "status": status,
                                "answer_to": user_message_id,
                            },
                        )
                        await self.cache.invalidate_conversation(conversation_id)
                        await self.cache.mark_conversation_write(conversation_id)
                        return

                async with self.session_factory() as session:
                    messages = MessageRepository(session)
                    await messages.create(
                        message_id=user_message_id,
                        conversation_id=conversation_id,
                        role="user",
                        content=user_message,
                        token_count=0,
                        metadata={"raw_token_count": user_raw_tokens},
                    )
                    await messages.create(
                        message_id=assistant_message_id,
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
                    conversations = ConversationRepository(session)
                    await conversations.touch(conversation_id)
                    await self._maybe_set_title(conversations, conversation_id, user_message)
                    await session.commit()
                await self._refresh_session_cache(conversation_id)
                await self.cache.invalidate_conversation(conversation_id)
                await self.cache.mark_conversation_write(conversation_id)
            except Exception:
                logger.warning("收尾落库失败 conversation_id=%s", conversation_id, exc_info=True)

    # ---- 生产端（独立任务，不随连接取消）----
    @staticmethod
    def _heartbeat_interval() -> float:
        stale_after = max(float(settings.CHAT_STREAM_STALE_SECONDS), 1.0)
        return max(0.2, min(float(HEARTBEAT_INTERVAL_SECONDS), stale_after / 3))

    async def _heartbeat(self, stream: StreamService, request_id: str) -> None:
        """生成期间独立刷新流元数据，覆盖首 token、摘要等长耗时阶段。"""
        while True:
            try:
                await stream.touch(request_id)
            except Exception:
                logger.warning("刷新流心跳失败 request_id=%s", request_id, exc_info=True)
            await asyncio.sleep(self._heartbeat_interval())

    async def _generate(
        self,
        stream: StreamService,
        request_id: str,
        conversation_id: int,
        assistant_parts: list[str],
    ) -> tuple[str, dict[str, Any] | None, int]:
        await self._ensure_session(conversation_id)
        summary, watermark = await self._maybe_compress_context(conversation_id)

        session_messages = await self.cache.get_session_messages(conversation_id)
        recent = [
            message
            for message in self._filter_context(session_messages)
            if int(message.get("id") or 0) > watermark
        ]
        prompt = self._build_prompt(recent, summary)

        final_finish = "stop"
        final_usage: dict[str, Any] | None = None
        buffer: list[str] = []
        buffered_chars = 0
        last_flush = time.monotonic()
        zero_readers_since: float | None = None

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
                    await stream.publish(request_id, "delta", {"content": "".join(buffer)})
                    buffer.clear()
                    buffered_chars = 0
                    last_flush = now

                if settings.CHAT_STREAM_ABANDON_TIMEOUT > 0:
                    if await stream.reader_count(request_id) == 0:
                        zero_readers_since = zero_readers_since or now
                        if now - zero_readers_since > settings.CHAT_STREAM_ABANDON_TIMEOUT:
                            raise _StreamAbandoned()
                    else:
                        zero_readers_since = None
            elif chunk.kind == "final":
                final_finish = chunk.finish_reason or "stop"
                final_usage = chunk.usage

        if buffer:
            await stream.publish(request_id, "delta", {"content": "".join(buffer)})

        prompt_tokens = int((final_usage or {}).get("prompt_tokens") or 0)
        if prompt_tokens <= 0:
            prompt_tokens = sum(count_tokens(message.get("content", "")) for message in prompt)
        return final_finish, final_usage, prompt_tokens

    async def _finalize_failed(
        self,
        stream: StreamService,
        request_id: str,
        conversation_id: int,
        user_id: int,
        user_message_id: int | None,
        user_message: str,
        user_raw_tokens: int,
        assistant_parts: list[str],
        status: str,
        error_code: str | None,
        persisted: bool,
    ) -> None:
        if user_message_id is not None:
            await self._persist_final_turn(
                request_id=request_id,
                conversation_id=conversation_id,
                user_id=user_id,
                user_message_id=user_message_id,
                user_message=user_message,
                user_raw_tokens=user_raw_tokens,
                assistant_content="".join(assistant_parts),
                status=status,
                error_code=error_code,
                already_persisted=persisted,
            )
        try:
            await stream.publish(
                request_id,
                "error",
                {
                    "code": error_code or "INTERRUPTED",
                    "message": "生成已中断" if status == "interrupted" else "聊天处理失败",
                },
            )
        except Exception:
            logger.warning("写入失败事件出错 request_id=%s", request_id, exc_info=True)
        try:
            await stream.set_status(request_id, STATUS_FAILED)
        except Exception:
            logger.warning("写入失败状态出错 request_id=%s", request_id, exc_info=True)

    async def run_generation(
        self,
        *,
        request_id: str,
        conversation_id: int,
        user_id: int,
        user_message: str,
        slot_counted: bool = False,
    ) -> None:
        """生产端：生成并写入 Redis Stream（由独立任务运行，不随连接取消）。"""
        stream = StreamService()
        user_raw_tokens = count_tokens(user_message)
        assistant_parts: list[str] = []
        user_message_id: int | None = None
        persisted = False
        heartbeat_task = asyncio.create_task(self._heartbeat(stream, request_id))
        try:
            # 分配 ID 并先入会话缓存（不落库），使本轮 prompt 含当前 user 消息
            user_message_id = await self._allocate_message_id()
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
            await stream.publish(
                request_id,
                "start",
                {"request_id": request_id, "user_message_id": user_message_id},
            )

            final_finish, final_usage, prompt_tokens = await self._generate(
                stream, request_id, conversation_id, assistant_parts
            )

            content = "".join(assistant_parts)
            assistant_message_id = await self._allocate_message_id()
            assistant_id, token_count = await self._persist_success_turn(
                request_id=request_id,
                conversation_id=conversation_id,
                user_id=user_id,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
                user_message=user_message,
                user_raw_tokens=user_raw_tokens,
                assistant_content=content,
                usage=final_usage,
                prompt_tokens=prompt_tokens,
            )
            persisted = True
            await stream.publish(
                request_id,
                "done",
                {
                    "assistant_message_id": assistant_id,
                    "token_count": token_count,
                    "finish_reason": final_finish,
                },
            )
            try:
                await stream.set_status(request_id, STATUS_COMPLETED)
            except Exception:
                logger.warning("写入完成状态失败 request_id=%s", request_id, exc_info=True)
        except _StreamAbandoned:
            logger.info("无订阅者超时，放弃生成 request_id=%s", request_id)
            await self._finalize_failed(
                stream,
                request_id,
                conversation_id,
                user_id,
                user_message_id,
                user_message,
                user_raw_tokens,
                assistant_parts,
                "interrupted",
                None,
                persisted,
            )
        except asyncio.CancelledError:
            logger.info("生成任务被取消 request_id=%s", request_id)
            await self._finalize_failed(
                stream,
                request_id,
                conversation_id,
                user_id,
                user_message_id,
                user_message,
                user_raw_tokens,
                assistant_parts,
                "interrupted",
                None,
                persisted,
            )
            raise
        except Exception as exc:
            logger.exception("聊天生成失败 request_id=%s", request_id)
            await self._finalize_failed(
                stream,
                request_id,
                conversation_id,
                user_id,
                user_message_id,
                user_message,
                user_raw_tokens,
                assistant_parts,
                "failed",
                getattr(exc, "code", "INTERNAL_ERROR"),
                persisted,
            )
        finally:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
            if slot_counted:
                await release_chat_slot(user_id)

    # ---- 断点续传 ----
    async def iter_stream(
        self, request_id: str, last_id: str | None = None
    ) -> AsyncIterator[tuple[str, dict[str, Any], str | None]]:
        """消费 Redis Stream，产出 (event, data, event_id)，支持从 last_id 续读。"""
        stream = StreamService()
        cursor = last_id or "0-0"
        poll = max(settings.CHAT_STREAM_POLL_MS, 10) / 1000
        init_deadline = time.monotonic() + STREAM_INIT_WAIT_SECONDS

        while True:
            entries = await stream.read_after(request_id, cursor)
            for entry_id, event, data in entries:
                cursor = entry_id
                yield event, data, entry_id
                if event in ("done", "error"):
                    return

            meta = await stream.get_meta(request_id)
            status = meta.get("status")

            if status in TERMINAL_STATUSES:
                while True:
                    remaining = await stream.read_after(request_id, cursor)
                    if not remaining:
                        break
                    for entry_id, event, data in remaining:
                        cursor = entry_id
                        yield event, data, entry_id
                        if event in ("done", "error"):
                            return
                break

            if status is None:
                if time.monotonic() > init_deadline:
                    yield (
                        "error",
                        {"code": "STREAM_NOT_FOUND", "message": "聊天流不存在或已过期"},
                        None,
                    )
                    break
                await asyncio.sleep(poll)
                continue

            if not entries and StreamService.is_stale(meta):
                yield (
                    "error",
                    {"code": "STREAM_STALE", "message": "生成状态已失效，请重试"},
                    None,
                )
                break

            if not entries:
                await asyncio.sleep(poll)
