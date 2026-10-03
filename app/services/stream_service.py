"""聊天流缓冲（Redis Stream），支持 `Last-Event-ID` 断点续传。

- `chat:stream:{request_id}`       Stream：条目字段 `event` / `data`，SSE 的 id = Stream ID
- `chat:stream:{request_id}:meta`  Hash：`user_id` / `conversation_id` / `status` / `updated_at`
- `chat:stream:{request_id}:readers` 计数器：订阅者数量（用于放弃判断）
"""

import json
import logging
import time
from typing import Any

from app.core.config import settings
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

STREAM_KEY = "chat:stream:{request_id}"
META_KEY = "chat:stream:{request_id}:meta"
READERS_KEY = "chat:stream:{request_id}:readers"

STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
TERMINAL_STATUSES = (STATUS_COMPLETED, STATUS_FAILED)


def _stream_key(request_id: str) -> str:
    return STREAM_KEY.format(request_id=request_id)


def _meta_key(request_id: str) -> str:
    return META_KEY.format(request_id=request_id)


def _readers_key(request_id: str) -> str:
    return READERS_KEY.format(request_id=request_id)


def _id_gt(a: str, b: str) -> bool:
    """比较 Redis Stream ID 大小（格式 ms-seq）。"""
    try:
        return (int(a.split("-")[0]), int(a.split("-")[1])) > (
            int(b.split("-")[0]),
            int(b.split("-")[1]),
        )
    except (ValueError, IndexError):
        return a > b


class StreamService:
    def __init__(self, redis: Any | None = None, ttl: int | None = None) -> None:
        self._redis = redis
        self._ttl = ttl

    @property
    def redis(self):
        return self._redis or get_redis()

    @property
    def ttl(self) -> int:
        return self._ttl if self._ttl is not None else settings.CHAT_STREAM_TTL

    async def init(self, request_id: str, user_id: int, conversation_id: int) -> None:
        key = _meta_key(request_id)
        await self.redis.hset(
            key,
            mapping={
                "user_id": str(user_id),
                "conversation_id": str(conversation_id),
                "status": STATUS_RUNNING,
                "updated_at": str(int(time.time())),
            },
        )
        await self.redis.expire(key, self.ttl)

    async def set_status(self, request_id: str, status: str) -> None:
        key = _meta_key(request_id)
        await self.redis.hset(
            key, mapping={"status": status, "updated_at": str(int(time.time()))}
        )
        await self.redis.expire(key, self.ttl)

    async def touch(self, request_id: str) -> None:
        """刷新心跳时间（生成期间周期调用）。"""
        key = _meta_key(request_id)
        await self.redis.hset(key, "updated_at", str(int(time.time())))
        await self.redis.expire(key, self.ttl)

    async def get_meta(self, request_id: str) -> dict[str, str]:
        return await self.redis.hgetall(_meta_key(request_id))

    async def publish(self, request_id: str, event: str, data: dict[str, Any]) -> str:
        entry_id = await self.redis.xadd(
            _stream_key(request_id),
            {"event": event, "data": json.dumps(data, ensure_ascii=False)},
        )
        await self.redis.expire(_stream_key(request_id), self.ttl)
        return entry_id

    async def read_after(
        self, request_id: str, last_id: str = "0-0", count: int = 100
    ) -> list[tuple[str, str, dict[str, Any]]]:
        min_id = "-" if last_id in ("", "0-0") else last_id
        entries = await self.redis.xrange(
            _stream_key(request_id), min=min_id, max="+", count=count
        )
        result: list[tuple[str, str, dict[str, Any]]] = []
        for entry_id, fields in entries:
            if last_id not in ("", "0-0") and not _id_gt(entry_id, last_id):
                continue
            event = fields.get("event", "message")
            raw = fields.get("data") or "{}"
            try:
                data = json.loads(raw)
            except ValueError:
                data = {"raw": raw}
            result.append((entry_id, event, data))
        return result

    async def reader_join(self, request_id: str) -> None:
        key = _readers_key(request_id)
        await self.redis.incr(key)
        await self.redis.expire(key, self.ttl)

    async def reader_leave(self, request_id: str) -> None:
        key = _readers_key(request_id)
        try:
            remaining = await self.redis.decr(key)
            if remaining <= 0:
                await self.redis.delete(key)
        except Exception:
            await self.redis.delete(key)

    async def reader_count(self, request_id: str) -> int:
        value = await self.redis.get(_readers_key(request_id))
        return int(value or 0)

    @staticmethod
    def is_stale(meta: dict[str, str]) -> bool:
        updated_at = meta.get("updated_at")
        if not updated_at:
            return True
        return (time.time() - int(updated_at)) > settings.CHAT_STREAM_STALE_SECONDS
