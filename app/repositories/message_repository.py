"""消息数据访问层。"""

from typing import Any

from sqlalchemy import delete, func, insert, select, union_all, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.message import Message
from app.models.message_archive import MessageArchive


def _message_select(model):
    """选择消息列（统一 label，便于热表与归档表 union）。"""
    return select(
        model.id.label("id"),
        model.conversation_id.label("conversation_id"),
        model.role.label("role"),
        model.content.label("content"),
        model.token_count.label("token_count"),
        model.created_at.label("created_at"),
        model.metadata_.label("metadata_"),
    )


def _message_union():
    return union_all(
        _message_select(Message), _message_select(MessageArchive)
    ).subquery()


def _to_message(row) -> Message:
    return Message(
        id=row.id,
        conversation_id=row.conversation_id,
        role=row.role,
        content=row.content,
        token_count=row.token_count,
        created_at=row.created_at,
        metadata_=row.metadata_,
    )


class MessageRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_by_conversation(
        self,
        conversation_id: int,
        *,
        page: int = 1,
        page_size: int = 50,
        order: str = "asc",
    ) -> tuple[list[Message], int]:
        total = await self.count_by_conversation(conversation_id)
        union = _message_union()
        order_by = union.c.id.asc() if order == "asc" else union.c.id.desc()
        result = await self.db.execute(
            select(union)
            .where(union.c.conversation_id == conversation_id)
            .order_by(order_by)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return [_to_message(row) for row in result.all()], total

    async def list_recent(self, conversation_id: int, limit: int) -> list[Message]:
        """按时间正序返回最近 limit 条消息。"""
        result = await self.db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.desc())
            .limit(limit)
        )
        return list(reversed(result.scalars().all()))

    async def count_by_conversation(self, conversation_id: int) -> int:
        hot = await self.db.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conversation_id)
        )
        archived = await self.db.scalar(
            select(func.count())
            .select_from(MessageArchive)
            .where(MessageArchive.conversation_id == conversation_id)
        )
        return int(hot or 0) + int(archived or 0)

    async def list_keyset_after(
        self, conversation_id: int, after_id: int, limit: int
    ) -> list[Message]:
        """keyset 分页（正序）：返回 id 大于 after_id 的前 limit 条。"""
        union = _message_union()
        result = await self.db.execute(
            select(union)
            .where(
                union.c.conversation_id == conversation_id,
                union.c.id > after_id,
            )
            .order_by(union.c.id.asc())
            .limit(limit)
        )
        return [_to_message(row) for row in result.all()]

    async def list_keyset_before(
        self, conversation_id: int, before_id: int, limit: int
    ) -> list[Message]:
        """keyset 分页（倒序）：返回 id 小于 before_id 的前 limit 条。"""
        union = _message_union()
        result = await self.db.execute(
            select(union)
            .where(
                union.c.conversation_id == conversation_id,
                union.c.id < before_id,
            )
            .order_by(union.c.id.desc())
            .limit(limit)
        )
        return [_to_message(row) for row in result.all()]

    async def list_after(self, conversation_id: int, after_id: int) -> list[Message]:
        """按时间正序返回 id 大于 after_id 的消息。"""
        result = await self.db.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation_id, Message.id > after_id
            )
            .order_by(Message.id.asc())
        )
        return list(result.scalars().all())

    async def sum_tokens_after(self, conversation_id: int, after_id: int) -> int:
        """统计 id 大于 after_id 的消息 token_count 之和。"""
        total = await self.db.scalar(
            select(func.coalesce(func.sum(Message.token_count), 0)).where(
                Message.conversation_id == conversation_id, Message.id > after_id
            )
        )
        return int(total or 0)

    async def get(self, message_id: int) -> Message | None:
        return await self.db.get(Message, message_id)

    async def max_id(self) -> int:
        """返回热表与归档表的最大 ID，供 Redis 分配器重新播种。"""
        hot = await self.db.scalar(select(func.max(Message.id)))
        archived = await self.db.scalar(select(func.max(MessageArchive.id)))
        return max(int(hot or 0), int(archived or 0))

    async def create(
        self,
        *,
        conversation_id: int,
        role: str,
        content: str,
        token_count: int | None = None,
        metadata: dict[str, Any] | None = None,
        message_id: int | None = None,
    ) -> Message:
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            token_count=token_count,
            metadata_=metadata,
        )
        if message_id is not None:
            message.id = message_id
        self.db.add(message)
        await self.db.flush()
        return message

    async def bulk_create(self, rows: list[dict[str, Any]]) -> None:
        """批量插入消息（写队列消费端使用，单条 INSERT 多行）。"""
        if not rows:
            return
        await self.db.execute(insert(Message), rows)
        await self.db.flush()

    async def delete(self, message: Message) -> None:
        await self.db.delete(message)
        await self.db.flush()

    async def exclude_from_context(
        self, message_id: int, raw_token_count: int
    ) -> None:
        """将消息移出上下文预算（token_count 归零，真实值存 metadata）。"""
        await self.db.execute(
            update(Message)
            .where(Message.id == message_id)
            .values(
                token_count=0,
                metadata_={"raw_token_count": raw_token_count},
            )
        )
        await self.db.flush()

    async def delete_by_conversation(self, conversation_id: int) -> None:
        """按会话批量删除热表与归档表消息（单条 DELETE，避免逐条加载）。"""
        await self.db.execute(
            delete(Message).where(Message.conversation_id == conversation_id)
        )
        await self.db.execute(
            delete(MessageArchive).where(
                MessageArchive.conversation_id == conversation_id
            )
        )
        await self.db.flush()

    async def delete_by_user(self, user_id: int) -> None:
        """按用户批量删除其所有会话下的热表与归档表消息。"""
        conversation_ids = select(Conversation.id).where(
            Conversation.user_id == user_id
        )
        await self.db.execute(
            delete(Message).where(Message.conversation_id.in_(conversation_ids))
        )
        await self.db.execute(
            delete(MessageArchive).where(
                MessageArchive.conversation_id.in_(conversation_ids)
            )
        )
        await self.db.flush()
