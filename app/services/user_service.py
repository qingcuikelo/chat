"""用户业务逻辑。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, UserNotFoundError
from app.core.security import hash_password
from app.models.user import User
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository
from app.repositories.user_repository import UserRepository
from app.schemas.user import UserUpdate
from app.services.cache_service import CacheService


class UserService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.users = UserRepository(db)
        self.conversations = ConversationRepository(db)
        self.messages = MessageRepository(db)
        self.cache = CacheService()

    async def update_me(self, current_user: User, payload: UserUpdate) -> User:
        user = await self.users.get(current_user.id)
        if user is None:
            raise UserNotFoundError()

        if payload.username and payload.username != user.username:
            if await self.users.get_by_username(payload.username):
                raise ConflictError(code="USERNAME_TAKEN", message="用户名已被占用")
        if payload.email and payload.email != user.email:
            if await self.users.get_by_email(payload.email):
                raise ConflictError(code="EMAIL_TAKEN", message="邮箱已被占用")

        fields: dict = {}
        if payload.username is not None:
            fields["username"] = payload.username
        if payload.email is not None:
            fields["email"] = payload.email
        if payload.password is not None:
            fields["password_hash"] = hash_password(payload.password)

        user = await self.users.update(user, **fields)
        await self.db.commit()
        await self.cache.invalidate_user(user.id)
        return user

    async def delete_me(self, current_user: User) -> None:
        user = await self.users.get(current_user.id)
        if user is None:
            raise UserNotFoundError()

        user_id = user.id
        # 批量删除消息与会话，避免 ORM 逐条级联在大数据量下 OOM
        await self.messages.delete_by_user(user_id)
        await self.conversations.delete_by_user(user_id)
        await self.users.delete(user)
        await self.db.commit()
        await self.cache.invalidate_user(user_id)
