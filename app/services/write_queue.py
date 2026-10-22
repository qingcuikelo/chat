"""聊天写队列（v3 WP8c）。

- `chat:write`  Stream：一轮聊天 = 一条消息（user + assistant 同批落库）
- `chat-writers` 消费组：worker 批量消费，单事务提交
- `chat:write:heartbeat`：消费者存活标记；缺失时生产者回退直接落库
- `chat:write:dead`：超过重试上限的死信
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from redis.exceptions import ResponseError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.database import AsyncSessionLocal, utcnow
from app.core.redis import get_redis
from app.core.text import generate_title
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.message_archive import MessageArchive
from app.repositories.message_repository import MessageRepository
from app.services.cache_service import CacheService

logger = logging.getLogger(__name__)

STREAM_KEY = "chat:write"
GROUP_NAME = "chat-writers"
HEARTBEAT_KEY = "chat:write:heartbeat"
DEAD_KEY = "chat:write:dead"
ATTEMPTS_KEY = "chat:write:attempts"

HEARTBEAT_TTL_SECONDS = 30
RECLAIM_IDLE_MS = 60_000
RECLAIM_INTERVAL_SECONDS = 60


@dataclass(slots=True)
class WriteTurn:
    request_id: str
    conversation_id: int
    user_id: int
    user_message_id: int
    assistant_message_id: int
    user_content: str
    assistant_content: str
    user_token_count: int
    assistant_token_count: int
    status: str = "completed"
    error_code: str | None = None
    user_metadata: dict[str, Any] | None = None
    assistant_metadata: dict[str, Any] | None = None
    created_at: datetime | None = None

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = (self.created_at or utcnow()).isoformat()
        return payload

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "WriteTurn":
        raw_created_at = data.get("created_at")
        return cls(
            request_id=str(data["request_id"]),
            conversation_id=int(data["conversation_id"]),
            user_id=int(data["user_id"]),
            user_message_id=int(data["user_message_id"]),
            assistant_message_id=int(data["assistant_message_id"]),
            user_content=data["user_content"],
            assistant_content=data.get("assistant_content") or "",
            user_token_count=int(data.get("user_token_count") or 0),
            assistant_token_count=int(data.get("assistant_token_count") or 0),
            status=data.get("status") or "completed",
            error_code=data.get("error_code"),
            user_metadata=data.get("user_metadata"),
            assistant_metadata=data.get("assistant_metadata"),
            created_at=(
                datetime.fromisoformat(raw_created_at) if raw_created_at else None
            ),
        )


async def _consumer_alive() -> bool:
    try:
        return bool(await get_redis().exists(HEARTBEAT_KEY))
    except Exception:
        return False


async def enqueue_turn(turn: WriteTurn) -> bool:
    """入队一轮落库；消费者不可用或入队失败时返回 False。"""
    if not await _consumer_alive():
        logger.warning(
            "写队列消费者心跳缺失，回退直接落库 conversation_id=%s",
            turn.conversation_id,
        )
        return False
    try:
        await get_redis().xadd(
            STREAM_KEY,
            {"payload": json.dumps(turn.to_payload(), ensure_ascii=False)},
        )
        return True
    except Exception:
        logger.warning(
            "写队列入队失败，回退直接落库 conversation_id=%s",
            turn.conversation_id,
            exc_info=True,
        )
        return False


async def ensure_group() -> None:
    try:
        await get_redis().xgroup_create(
            STREAM_KEY, GROUP_NAME, id="0", mkstream=True
        )
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def read_batch(
    consumer: str,
    *,
    max_size: int | None = None,
    max_wait_ms: int | None = None,
) -> list[tuple[str, dict[str, str]]]:
    redis = get_redis()
    size = max(1, max_size if max_size is not None else settings.WRITE_QUEUE_BATCH_SIZE)
    wait_ms = max(
        0,
        max_wait_ms
        if max_wait_ms is not None
        else settings.WRITE_QUEUE_BATCH_MAX_WAIT_MS,
    )
    deadline = time.monotonic() + wait_ms / 1000
    entries: list[tuple[str, dict[str, str]]] = []

    while len(entries) < size:
        remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
        result = await redis.xreadgroup(
            GROUP_NAME,
            consumer,
            {STREAM_KEY: ">"},
            count=size - len(entries),
            block=remaining_ms,
        )
        if not result:
            break
        for _stream, items in result:
            entries.extend(items)
        if len(entries) >= size or time.monotonic() >= deadline:
            break
    return entries


async def _ack(entry_ids: list[str]) -> None:
    if not entry_ids:
        return
    try:
        pipe = get_redis().pipeline()
        for entry_id in entry_ids:
            pipe.xack(STREAM_KEY, GROUP_NAME, entry_id)
            pipe.xdel(STREAM_KEY, entry_id)
        await pipe.execute()
    except Exception:
        logger.warning("写队列 ACK 失败 entry_ids=%s", entry_ids, exc_info=True)


async def _clear_attempts(entry_ids: list[str]) -> None:
    if not entry_ids:
        return
    try:
        await get_redis().hdel(ATTEMPTS_KEY, *entry_ids)
    except Exception:
        pass


async def _dead_letter(entry_id: str, payload: str, reason: str) -> None:
    try:
        await get_redis().xadd(
            DEAD_KEY,
            {"payload": payload, "reason": reason, "source_id": entry_id},
        )
    except Exception:
        logger.warning("写死信失败 entry_id=%s", entry_id, exc_info=True)


async def process_turns(
    turns: list[WriteTurn],
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """批量落库：user + assistant 多行 INSERT，单事务提交。"""
    if not turns:
        return 0

    factory = session_factory or AsyncSessionLocal
    cache_ids: set[int] = set()

    async with factory() as session:
        conversation_ids = {turn.conversation_id for turn in turns}
        conversations = {
            conversation.id: conversation
            for conversation in (
                await session.execute(
                    select(Conversation).where(
                        Conversation.id.in_(conversation_ids)
                    )
                )
            ).scalars()
        }
        valid = [t for t in turns if t.conversation_id in conversations]
        if not valid:
            return 0

        message_ids = [t.user_message_id for t in valid] + [
            t.assistant_message_id for t in valid
        ]
        existing = set(
            (
                await session.execute(
                    select(Message.id).where(Message.id.in_(message_ids))
                )
            ).scalars()
        )
        existing |= set(
            (
                await session.execute(
                    select(MessageArchive.id).where(
                        MessageArchive.id.in_(message_ids)
                    )
                )
            ).scalars()
        )

        rows: list[dict[str, Any]] = []
        for turn in valid:
            created_at = turn.created_at or utcnow()
            if turn.user_message_id not in existing:
                rows.append(
                    {
                        "id": turn.user_message_id,
                        "conversation_id": turn.conversation_id,
                        "role": "user",
                        "content": turn.user_content,
                        "token_count": turn.user_token_count,
                        "metadata_": turn.user_metadata,
                        "created_at": created_at,
                    }
                )
                existing.add(turn.user_message_id)
            if turn.assistant_message_id not in existing:
                rows.append(
                    {
                        "id": turn.assistant_message_id,
                        "conversation_id": turn.conversation_id,
                        "role": "assistant",
                        "content": turn.assistant_content,
                        "token_count": turn.assistant_token_count,
                        "metadata_": turn.assistant_metadata,
                        "created_at": created_at,
                    }
                )
                existing.add(turn.assistant_message_id)

        if rows:
            await MessageRepository(session).bulk_create(rows)

        by_conversation: dict[int, list[WriteTurn]] = {}
        for turn in valid:
            by_conversation.setdefault(turn.conversation_id, []).append(turn)

        for conversation_id, conversation_turns in by_conversation.items():
            conversation = conversations[conversation_id]
            latest = max(
                (turn.created_at or utcnow()) for turn in conversation_turns
            )
            if conversation.updated_at is None or latest > conversation.updated_at:
                conversation.updated_at = latest
            if not conversation.title:
                first_content = next(
                    (t.user_content for t in conversation_turns if t.user_content),
                    "",
                )
                title = generate_title(first_content)
                if title:
                    conversation.title = title

        await session.commit()
        cache_ids = set(by_conversation)

    cache = CacheService()
    for conversation_id in cache_ids:
        await cache.invalidate_conversation(conversation_id)

    return len(valid)


async def _parse_entries(
    entries: list[tuple[str, dict[str, str]]],
) -> tuple[list[tuple[str, WriteTurn]], list[tuple[str, dict[str, str]]]]:
    parsed: list[tuple[str, WriteTurn]] = []
    invalid: list[tuple[str, dict[str, str]]] = []
    for entry_id, fields in entries:
        try:
            payload = json.loads(fields.get("payload") or "{}")
            parsed.append((entry_id, WriteTurn.from_payload(payload)))
        except Exception:
            logger.warning("写队列载荷解析失败 entry_id=%s", entry_id, exc_info=True)
            invalid.append((entry_id, fields))
    return parsed, invalid


async def _fallback_individual(
    parsed: list[tuple[str, WriteTurn]],
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """批量失败后逐条重试；超过上限的进入死信。"""
    written = 0
    redis = get_redis()
    for entry_id, turn in parsed:
        try:
            written += await process_turns([turn], session_factory)
            await _ack([entry_id])
            await _clear_attempts([entry_id])
        except Exception:
            logger.warning(
                "写队列单条落库失败 entry_id=%s conversation_id=%s",
                entry_id,
                turn.conversation_id,
                exc_info=True,
            )
            try:
                attempts = await redis.hincrby(ATTEMPTS_KEY, entry_id, 1)
            except Exception:
                attempts = 0
            if attempts >= settings.WRITE_QUEUE_MAX_ATTEMPTS:
                await _dead_letter(
                    entry_id,
                    json.dumps(turn.to_payload(), ensure_ascii=False),
                    f"max_attempts:{attempts}",
                )
                await _ack([entry_id])
                await _clear_attempts([entry_id])
    return written


async def handle_entries(
    entries: list[tuple[str, dict[str, str]]],
    *,
    consumer: str = "worker",
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    if not entries:
        return 0

    parsed, invalid = await _parse_entries(entries)
    for entry_id, fields in invalid:
        await _dead_letter(
            entry_id, fields.get("payload") or "", "invalid_payload"
        )
        await _ack([entry_id])

    if not parsed:
        return 0

    try:
        written = await process_turns(
            [turn for _, turn in parsed], session_factory
        )
    except Exception:
        logger.warning("写队列批量落库失败，改为逐条重试", exc_info=True)
        return await _fallback_individual(parsed, session_factory)

    entry_ids = [entry_id for entry_id, _ in parsed]
    await _ack(entry_ids)
    await _clear_attempts(entry_ids)
    return written


async def consume_once(
    consumer: str = "test",
    *,
    max_size: int | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """消费一批（测试/运维手动触发）。"""
    await ensure_group()
    entries = await read_batch(consumer, max_size=max_size, max_wait_ms=0)
    return await handle_entries(
        entries, consumer=consumer, session_factory=session_factory
    )


async def reclaim_pending(consumer: str) -> int:
    try:
        result = await get_redis().xautoclaim(
            STREAM_KEY,
            GROUP_NAME,
            consumer,
            min_idle_time=RECLAIM_IDLE_MS,
            start_id="0-0",
            count=settings.WRITE_QUEUE_BATCH_SIZE,
        )
    except ResponseError:
        return 0
    entries = result[1] if len(result) > 1 else []
    return await handle_entries(entries, consumer=consumer)


async def run_consumer(stop_event: asyncio.Event) -> None:
    consumer = f"{socket.gethostname()}-{os.getpid()}"
    logger.info("写队列消费者启动 consumer=%s", consumer)
    last_reclaim = 0.0

    while not stop_event.is_set():
        try:
            await ensure_group()
            await get_redis().set(
                HEARTBEAT_KEY, "1", ex=HEARTBEAT_TTL_SECONDS
            )
            now = time.monotonic()
            if now - last_reclaim >= RECLAIM_INTERVAL_SECONDS:
                await reclaim_pending(consumer)
                last_reclaim = now

            entries = await read_batch(consumer)
            if entries:
                await handle_entries(entries, consumer=consumer)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("写队列消费循环异常", exc_info=True)
            await asyncio.sleep(1)

    logger.info("写队列消费者停止 consumer=%s", consumer)


_stop_event: asyncio.Event | None = None


async def start_consumer() -> asyncio.Task:
    global _stop_event
    _stop_event = asyncio.Event()
    return asyncio.create_task(
        run_consumer(_stop_event), name="chat-write-consumer"
    )


async def stop_consumer(task: asyncio.Task | None) -> None:
    global _stop_event
    if _stop_event is not None:
        _stop_event.set()
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    _stop_event = None
