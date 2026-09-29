"""会话业务逻辑。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConversationNotFoundError
from app.models.conversation import Conversation
from app.models.message import Message
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository
from app.schemas.conversation import ConversationCreate


class ConversationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.conversations = ConversationRepository(db)
        self.messages = MessageRepository(db)

    async def create(self, user_id: int, payload: ConversationCreate) -> Conversation:
        conversation = await self.conversations.create(
            user_id=user_id, title=payload.title
        )
        await self.db.commit()
        return conversation

    async def list(
        self, user_id: int, *, page: int = 1, page_size: int = 20
    ) -> tuple[list[Conversation], int]:
        return await self.conversations.list_by_user(
            user_id, page=page, page_size=page_size
        )

    async def get(self, conversation_id: int, user_id: int) -> Conversation:
        conversation = await self.conversations.get_for_user(conversation_id, user_id)
        if conversation is None:
            raise ConversationNotFoundError()
        return conversation

    async def update_title(
        self, conversation_id: int, user_id: int, title: str
    ) -> Conversation:
        conversation = await self.get(conversation_id, user_id)
        conversation = await self.conversations.update_title(conversation, title)
        await self.db.commit()
        return conversation

    async def delete(self, conversation_id: int, user_id: int) -> None:
        conversation = await self.get(conversation_id, user_id)
        await self.conversations.delete(conversation)
        await self.db.commit()

    async def list_messages(
        self,
        conversation_id: int,
        user_id: int,
        *,
        page: int = 1,
        page_size: int = 50,
        order: str = "asc",
    ) -> tuple[list[Message], int]:
        await self.get(conversation_id, user_id)
        return await self.messages.list_by_conversation(
            conversation_id, page=page, page_size=page_size, order=order
        )
