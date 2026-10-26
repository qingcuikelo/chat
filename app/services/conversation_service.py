"""会话业务逻辑。"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConversationNotFoundError, InvalidRequestError
from app.models.conversation import Conversation
from app.models.message import Message
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository
from app.schemas.conversation import ConversationCreate, ConversationUpdate
from app.services.cache_service import CacheService


class ConversationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.conversations = ConversationRepository(db)
        self.messages = MessageRepository(db)
        self.cache = CacheService()

    async def create(self, user_id: int, payload: ConversationCreate) -> Conversation:
        conversation = await self.conversations.create(user_id=user_id, title=payload.title)
        await self.db.commit()
        await self.cache.mark_conversation_write(conversation.id)
        return conversation

    async def list_conversations(
        self,
        user_id: int,
        *,
        page: int = 1,
        page_size: int = 20,
        q: str | None = None,
        archived: bool = False,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> tuple[list[Conversation], int, str | None]:
        size = limit or page_size
        query = q.strip() if q else None
        cursor_position = self._decode_cursor(cursor) if cursor else None
        items, total = await self.conversations.list_by_user(
            user_id,
            page=page,
            page_size=size,
            q=query or None,
            archived=archived,
            cursor=cursor_position,
        )

        if cursor_position is not None:
            has_more = len(items) > size
            if has_more:
                items = items[:size]
            next_cursor = self._encode_cursor(items[-1]) if has_more and items else None
        else:
            offset = (page - 1) * size
            next_cursor = (
                self._encode_cursor(items[-1]) if items and offset + len(items) < total else None
            )
        return items, total, next_cursor

    async def get(self, conversation_id: int, user_id: int) -> Conversation:
        """获取数据库中的会话（供需要变更的场景使用）。"""
        conversation = await self.conversations.get_for_user(conversation_id, user_id)
        if conversation is None:
            raise ConversationNotFoundError()
        return conversation

    async def get_detail(self, conversation_id: int, user_id: int) -> Conversation:
        """读取会话详情，优先使用缓存。"""
        cached = await self.cache.get_conversation(conversation_id)
        if cached is not None:
            if cached.user_id != user_id:
                raise ConversationNotFoundError()
            return cached

        conversation = await self.get(conversation_id, user_id)
        await self.cache.set_conversation(conversation)
        return conversation

    async def update(
        self, conversation_id: int, user_id: int, payload: ConversationUpdate
    ) -> Conversation:
        conversation = await self.get(conversation_id, user_id)
        fields = payload.model_dump(exclude_unset=True)
        conversation = await self.conversations.update_fields(
            conversation,
            title=fields.get("title"),
            is_pinned=fields.get("is_pinned"),
            is_archived=fields.get("is_archived"),
        )
        await self.db.commit()
        await self.cache.invalidate_conversation(conversation_id)
        await self.cache.mark_conversation_write(conversation_id)
        return conversation

    async def delete(self, conversation_id: int, user_id: int) -> None:
        await self.get(conversation_id, user_id)
        await self.messages.delete_by_conversation(conversation_id)
        await self.conversations.delete_by_id(conversation_id)
        await self.db.commit()
        await self.cache.invalidate_conversation(conversation_id)
        await self.cache.clear_session(conversation_id)
        await self.cache.mark_conversation_write(conversation_id)

    async def list_messages(
        self,
        conversation_id: int,
        user_id: int,
        *,
        page: int = 1,
        page_size: int = 50,
        order: str = "asc",
        cursor: int | None = None,
    ) -> tuple[list[Message], int]:
        await self.get(conversation_id, user_id)
        if cursor is not None:
            if order == "asc":
                items = await self.messages.list_keyset_after(conversation_id, cursor, page_size)
            else:
                items = await self.messages.list_keyset_before(conversation_id, cursor, page_size)
            total = await self.messages.count_by_conversation(conversation_id)
            return items, total
        return await self.messages.list_by_conversation(
            conversation_id, page=page, page_size=page_size, order=order
        )

    async def export(
        self, conversation_id: int, user_id: int
    ) -> tuple[Conversation, list[Message]]:
        conversation = await self.get(conversation_id, user_id)
        messages = await self.messages.list_all(conversation_id)
        return conversation, messages

    @staticmethod
    def _encode_cursor(conversation: Conversation) -> str:
        payload = {
            "p": int(conversation.is_pinned),
            "u": conversation.updated_at.isoformat(),
            "i": conversation.id,
        }
        raw = json.dumps(payload, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str) -> tuple[bool, datetime, int]:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
            if not isinstance(data, dict):
                raise ValueError("cursor is not an object")
            pinned = int(data["p"])
            updated_at = datetime.fromisoformat(str(data["u"]))
            last_id = int(data["i"])
            if pinned not in (0, 1) or last_id <= 0 or updated_at.tzinfo is not None:
                raise ValueError("invalid cursor values")
        except (ValueError, TypeError, KeyError, UnicodeDecodeError, binascii.Error) as exc:
            raise InvalidRequestError("分页游标无效") from exc
        return bool(pinned), updated_at, last_id
