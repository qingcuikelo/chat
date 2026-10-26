"""会话数据访问层。"""

from datetime import datetime

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import utcnow
from app.models.conversation import Conversation


class ConversationRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_by_user(
        self,
        user_id: int,
        *,
        page: int = 1,
        page_size: int = 20,
        q: str | None = None,
        archived: bool = False,
        cursor: tuple[bool, datetime, int] | None = None,
    ) -> tuple[list[Conversation], int]:
        filters = [
            Conversation.user_id == user_id,
            Conversation.is_archived == archived,
        ]
        if q:
            filters.append(Conversation.title.contains(q, autoescape=True))

        total = await self.db.scalar(select(func.count()).select_from(Conversation).where(*filters))
        statement = (
            select(Conversation)
            .where(*filters)
            .order_by(
                Conversation.is_pinned.desc(),
                Conversation.updated_at.desc(),
                Conversation.id.desc(),
            )
        )
        if cursor is not None:
            pinned, updated_at, last_id = cursor
            if pinned:
                after_position = or_(
                    Conversation.is_pinned.is_(False),
                    and_(
                        Conversation.is_pinned.is_(True),
                        or_(
                            Conversation.updated_at < updated_at,
                            and_(
                                Conversation.updated_at == updated_at,
                                Conversation.id < last_id,
                            ),
                        ),
                    ),
                )
            else:
                after_position = and_(
                    Conversation.is_pinned.is_(False),
                    or_(
                        Conversation.updated_at < updated_at,
                        and_(
                            Conversation.updated_at == updated_at,
                            Conversation.id < last_id,
                        ),
                    ),
                )
            statement = statement.where(after_position).limit(page_size + 1)
        else:
            statement = statement.offset((page - 1) * page_size).limit(page_size)

        result = await self.db.execute(statement)
        return list(result.scalars().all()), int(total or 0)

    async def get_for_user(self, conversation_id: int, user_id: int) -> Conversation | None:
        result = await self.db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id, Conversation.user_id == user_id
            )
        )
        return result.scalar_one_or_none()

    async def get(self, conversation_id: int) -> Conversation | None:
        return await self.db.get(Conversation, conversation_id)

    async def create(self, *, user_id: int, title: str | None) -> Conversation:
        conversation = Conversation(user_id=user_id, title=title)
        self.db.add(conversation)
        await self.db.flush()
        return conversation

    async def update_fields(
        self,
        conversation: Conversation,
        *,
        title: str | None = None,
        is_pinned: bool | None = None,
        is_archived: bool | None = None,
    ) -> Conversation:
        values: dict[str, object] = {}
        if title is not None:
            values["title"] = title
            values["updated_at"] = utcnow()
        if is_pinned is not None:
            values["is_pinned"] = is_pinned
        if is_archived is not None:
            if is_archived and not conversation.is_archived:
                values["archived_at"] = utcnow()
            elif not is_archived:
                values["archived_at"] = None
            values["is_archived"] = is_archived
        if "updated_at" not in values:
            values["updated_at"] = Conversation.updated_at

        await self.db.execute(
            update(Conversation).where(Conversation.id == conversation.id).values(**values)
        )
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

    async def update_summary(self, conversation_id: int, summary: str, watermark: int) -> None:
        """乐观更新滚动摘要与水位线（仅在推进时写入）。"""
        await self.db.execute(
            update(Conversation)
            .where(
                Conversation.id == conversation_id,
                or_(
                    Conversation.summary_until_message_id.is_(None),
                    Conversation.summary_until_message_id < watermark,
                ),
            )
            .values(summary=summary, summary_until_message_id=watermark)
        )
        await self.db.flush()

    async def delete(self, conversation: Conversation) -> None:
        await self.db.delete(conversation)
        await self.db.flush()

    async def delete_by_id(self, conversation_id: int) -> None:
        await self.db.execute(delete(Conversation).where(Conversation.id == conversation_id))
        await self.db.flush()

    async def delete_by_user(self, user_id: int) -> None:
        await self.db.execute(delete(Conversation).where(Conversation.user_id == user_id))
        await self.db.flush()
