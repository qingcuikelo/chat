"""消息数据访问层。"""

from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import Message


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
        total = await self.db.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conversation_id)
        )
        order_by = Message.id.asc() if order == "asc" else Message.id.desc()
        result = await self.db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(order_by)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), int(total or 0)

    async def list_recent(self, conversation_id: int, limit: int) -> list[Message]:
        """按时间正序返回最近 limit 条消息。"""
        result = await self.db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.desc())
            .limit(limit)
        )
        return list(reversed(result.scalars().all()))

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

    async def create(
        self,
        *,
        conversation_id: int,
        role: str,
        content: str,
        token_count: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            token_count=token_count,
            metadata_=metadata,
        )
        self.db.add(message)
        await self.db.flush()
        return message

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
        messages, _ = await self.list_by_conversation(
            conversation_id, page=1, page_size=10**9
        )
        for message in messages:
            await self.db.delete(message)
        await self.db.flush()
