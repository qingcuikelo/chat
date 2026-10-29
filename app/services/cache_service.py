"""Redis 缓存：用户 / 会话实体缓存，以及聊天会话上下文缓存。

对应设计文档第 5 节：
- `chat:session:{conversation_id}`  List    TTL=REDIS_SESSION_TTL  聊天过程快速访问的消息上下文
- `user:{user_id}`                   Hash    TTL=REDIS_CACHE_TTL    用户实体缓存（不含敏感字段）
- `conversation:{conversation_id}`   Hash    TTL=REDIS_CACHE_TTL    会话实体缓存

所有缓存操作失败都会被吞掉并降级到数据库，不影响主流程。
"""

import json
import logging
from datetime import datetime
from typing import Any

from app.core.config import settings
from app.core.redis import get_redis
from app.models.conversation import Conversation
from app.models.user import User

logger = logging.getLogger(__name__)

SESSION_KEY = "chat:session:{conversation_id}"
USER_KEY = "user:{user_id}"
CONVERSATION_KEY = "conversation:{conversation_id}"
RECENT_WRITE_KEY = "conv:recent_write:{conversation_id}"


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def user_to_cache(user: User) -> dict[str, str]:
    return {
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "pending_email": user.pending_email or "",
        "token_version": str(user.token_version or 0),
        "email_verified": "1" if user.email_verified else "0",
        "email_verified_at": _iso(user.email_verified_at),
        "created_at": _iso(user.created_at),
        "updated_at": _iso(user.updated_at),
    }


def user_from_cache(data: dict[str, str]) -> User:
    return User(
        id=int(data["id"]),
        username=data["username"],
        email=data["email"],
        pending_email=data.get("pending_email") or None,
        token_version=int(data.get("token_version") or 0),
        email_verified=data.get("email_verified") == "1",
        email_verified_at=_parse_dt(data.get("email_verified_at")),
        created_at=_parse_dt(data.get("created_at")),
        updated_at=_parse_dt(data.get("updated_at")),
    )


def conversation_to_cache(conversation: Conversation) -> dict[str, str]:
    return {
        "id": str(conversation.id),
        "user_id": str(conversation.user_id),
        "title": conversation.title or "",
        "is_pinned": "1" if conversation.is_pinned else "0",
        "is_archived": "1" if conversation.is_archived else "0",
        "archived_at": _iso(conversation.archived_at),
        "created_at": _iso(conversation.created_at),
        "updated_at": _iso(conversation.updated_at),
    }


def conversation_from_cache(data: dict[str, str]) -> Conversation:
    return Conversation(
        id=int(data["id"]),
        user_id=int(data["user_id"]),
        title=data.get("title") or None,
        is_pinned=data.get("is_pinned") == "1",
        is_archived=data.get("is_archived") == "1",
        archived_at=_parse_dt(data.get("archived_at")),
        created_at=_parse_dt(data.get("created_at")),
        updated_at=_parse_dt(data.get("updated_at")),
    )


class CacheService:
    def __init__(self, redis: Any | None = None) -> None:
        self._redis = redis

    @property
    def redis(self):
        return self._redis or get_redis()

    # ---- 用户缓存 ----
    async def get_user(self, user_id: int) -> User | None:
        try:
            data = await self.redis.hgetall(USER_KEY.format(user_id=user_id))
        except Exception:
            logger.warning("读取用户缓存失败 user_id=%s", user_id, exc_info=True)
            return None
        return user_from_cache(data) if data else None

    async def set_user(self, user: User) -> None:
        key = USER_KEY.format(user_id=user.id)
        try:
            await self.redis.hset(key, mapping=user_to_cache(user))
            if settings.REDIS_CACHE_TTL > 0:
                await self.redis.expire(key, settings.REDIS_CACHE_TTL)
        except Exception:
            logger.warning("写入用户缓存失败 user_id=%s", user.id, exc_info=True)

    async def invalidate_user(self, user_id: int) -> None:
        try:
            await self.redis.delete(USER_KEY.format(user_id=user_id))
        except Exception:
            logger.warning("失效用户缓存失败 user_id=%s", user_id, exc_info=True)

    # ---- 会话实体缓存 ----
    async def get_conversation(self, conversation_id: int) -> Conversation | None:
        try:
            data = await self.redis.hgetall(
                CONVERSATION_KEY.format(conversation_id=conversation_id)
            )
        except Exception:
            logger.warning("读取会话缓存失败 conversation_id=%s", conversation_id, exc_info=True)
            return None
        return conversation_from_cache(data) if data else None

    async def set_conversation(self, conversation: Conversation) -> None:
        key = CONVERSATION_KEY.format(conversation_id=conversation.id)
        try:
            await self.redis.hset(key, mapping=conversation_to_cache(conversation))
            if settings.REDIS_CACHE_TTL > 0:
                await self.redis.expire(key, settings.REDIS_CACHE_TTL)
        except Exception:
            logger.warning("写入会话缓存失败 conversation_id=%s", conversation.id, exc_info=True)

    async def invalidate_conversation(self, conversation_id: int) -> None:
        try:
            await self.redis.delete(CONVERSATION_KEY.format(conversation_id=conversation_id))
        except Exception:
            logger.warning("失效会话缓存失败 conversation_id=%s", conversation_id, exc_info=True)

    # ---- 读写分离一致性标记 ----
    async def mark_conversation_write(self, conversation_id: int) -> None:
        """标记会话刚发生写入，短窗口内该会话的读取回退主库。"""
        ttl = settings.DB_REPLICA_STICKY_SECONDS
        if not settings.DB_REPLICA_ENABLED or ttl <= 0:
            return
        try:
            await self.redis.set(
                RECENT_WRITE_KEY.format(conversation_id=conversation_id), "1", ex=ttl
            )
        except Exception:
            logger.warning(
                "写入会话一致性标记失败 conversation_id=%s",
                conversation_id,
                exc_info=True,
            )

    async def is_recently_written(self, conversation_id: int) -> bool:
        try:
            return bool(
                await self.redis.exists(RECENT_WRITE_KEY.format(conversation_id=conversation_id))
            )
        except Exception:
            # Redis 不可用时宁可回退主库
            return True

    # ---- 聊天会话上下文缓存 ----
    async def get_session_messages(self, conversation_id: int) -> list[dict[str, Any]]:
        try:
            raw = await self.redis.lrange(
                SESSION_KEY.format(conversation_id=conversation_id), 0, -1
            )
        except Exception:
            logger.warning(
                "读取聊天会话缓存失败 conversation_id=%s", conversation_id, exc_info=True
            )
            return []

        messages: list[dict[str, Any]] = []
        for item in raw:
            try:
                messages.append(json.loads(item))
            except (TypeError, ValueError):
                continue
        return messages

    async def set_session_messages(
        self, conversation_id: int, messages: list[dict[str, Any]]
    ) -> None:
        key = SESSION_KEY.format(conversation_id=conversation_id)
        try:
            pipe = self.redis.pipeline()
            pipe.delete(key)
            if messages:
                pipe.rpush(key, *[json.dumps(m, ensure_ascii=False) for m in messages])
                if settings.REDIS_SESSION_TTL > 0:
                    pipe.expire(key, settings.REDIS_SESSION_TTL)
            await pipe.execute()
        except Exception:
            logger.warning(
                "重建聊天会话缓存失败 conversation_id=%s", conversation_id, exc_info=True
            )

    async def push_session_message(self, conversation_id: int, message: dict[str, Any]) -> None:
        key = SESSION_KEY.format(conversation_id=conversation_id)
        try:
            pipe = self.redis.pipeline()
            pipe.rpush(key, json.dumps(message, ensure_ascii=False))
            pipe.ltrim(key, -settings.CHAT_CONTEXT_MAX_MESSAGES, -1)
            if settings.REDIS_SESSION_TTL > 0:
                pipe.expire(key, settings.REDIS_SESSION_TTL)
            await pipe.execute()
        except Exception:
            logger.warning(
                "追加聊天会话缓存失败 conversation_id=%s", conversation_id, exc_info=True
            )

    async def clear_session(self, conversation_id: int) -> None:
        try:
            await self.redis.delete(SESSION_KEY.format(conversation_id=conversation_id))
        except Exception:
            logger.warning(
                "清理聊天会话缓存失败 conversation_id=%s", conversation_id, exc_info=True
            )
