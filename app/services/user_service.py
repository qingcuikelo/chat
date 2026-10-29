"""用户业务逻辑。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    ConflictError,
    InvalidRequestError,
    ServiceUnavailableError,
    UserNotFoundError,
)
from app.core.security import hash_password, verify_password
from app.models.user import User
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository
from app.repositories.user_repository import UserRepository
from app.schemas.user import UserUpdate
from app.services.cache_service import CacheService
from app.services.mail_service import dispatch_verification_email
from app.services.token_service import TokenService


class UserService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.users = UserRepository(db)
        self.conversations = ConversationRepository(db)
        self.messages = MessageRepository(db)
        self.cache = CacheService()
        self.tokens = TokenService()

    async def update_me(
        self,
        current_user: User,
        payload: UserUpdate,
        *,
        access_jti: str | None = None,
        access_ttl: int = 0,
    ) -> User:
        user = await self.users.get(current_user.id)
        if user is None:
            raise UserNotFoundError()

        if payload.username and payload.username != user.username:
            if await self.users.get_by_username(payload.username):
                raise ConflictError(code="USERNAME_TAKEN", message="用户名已被占用")
        email_changed = payload.email is not None and payload.email != user.email
        if email_changed and payload.email is not None:
            if await self.users.get_by_email(str(payload.email)):
                raise ConflictError(code="EMAIL_TAKEN", message="邮箱已被占用")

        password_changed = payload.password is not None
        requires_current = password_changed or email_changed
        if requires_current:
            if not verify_password(payload.current_password or "", user.password_hash):
                raise InvalidRequestError("修改密码或邮箱需要提供正确的当前密码")

        fields: dict = {}
        if payload.username is not None:
            fields["username"] = payload.username
        if password_changed:
            fields["password_hash"] = hash_password(payload.password or "")
            fields["token_version"] = (user.token_version or 0) + 1
        if email_changed:
            if settings.EMAIL_VERIFICATION_REQUIRED:
                fields["pending_email"] = str(payload.email)
            else:
                fields["email"] = str(payload.email)
                fields["pending_email"] = None
                fields["token_version"] = (user.token_version or 0) + 1

        revoke_sessions = password_changed or (
            email_changed and not settings.EMAIL_VERIFICATION_REQUIRED
        )
        if revoke_sessions:
            try:
                await self.tokens.revoke_user_refresh_tokens(user.id, raise_on_failure=True)
                if access_jti:
                    await self.tokens.blacklist_access(
                        access_jti, access_ttl, raise_on_failure=True
                    )
            except Exception as exc:
                raise ServiceUnavailableError("会话撤销失败，请稍后重试") from exc

        user = await self.users.update(user, **fields)
        await self.db.commit()
        await self.cache.invalidate_user(user.id)

        if email_changed and settings.EMAIL_VERIFICATION_REQUIRED:
            await dispatch_verification_email(
                user, email=str(payload.email), purpose="email_change"
            )
        return user

    async def cancel_email_change(self, current_user: User) -> None:
        user = await self.users.get(current_user.id)
        if user is None:
            raise UserNotFoundError()
        if not user.pending_email:
            return
        user.pending_email = None
        await self.db.commit()
        await self.cache.invalidate_user(user.id)

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
