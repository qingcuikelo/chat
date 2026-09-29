"""会话数据访问层。"""

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import utcnow
from app.models.conversation import Conversation


class ConversationRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_by_user(
        self, user_id: int, *, page: int = 1, page_size: int = 20
    ) -> tuple[list[Conversation], int]:
        total = await self.db.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.user_id == user_id)
        )
        result = await self.db.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), int(total or 0)

    async def get_for_user(
        self, conversation_id: int, user_id: int
    ) -> Conversation | None:
        result = await self.db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id, Conversation.user_id == user_id
            )
        )
        return result.scalar_one_or_none()

    async def create(self, *, user_id: int, title: str | None) -> Conversation:
        conversation = Conversation(user_id=user_id, title=title)
        self.db.add(conversation)
        await self.db.flush()
        await self.db.refresh(conversation)
        return conversation

    async def update_title(self, conversation: Conversation, title: str) -> Conversation:
        conversation.title = title
        await self.db.flush()
        await self.db.refresh(conversation)
        return conversation

    async def touch(self, conversation_id: int) -> None:
        """更新会话活跃时间（用于会话列表排序）。"""
        await self.db.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(updated_at=utcnow())
        )
        await self.db.flush()

    async def delete(self, conversation: Conversation) -> None:
        await self.db.delete(conversation)
        await self.db.flush()
