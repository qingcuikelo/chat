"""用户业务逻辑。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.core.security import hash_password
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.user import UserUpdate


class UserService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.users = UserRepository(db)

    async def update_me(self, user: User, payload: UserUpdate) -> User:
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
        return user

    async def delete_me(self, user: User) -> None:
        await self.users.delete(user)
        await self.db.commit()
