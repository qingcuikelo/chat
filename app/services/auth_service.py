"""认证业务逻辑。"""

import jwt

from app.core.config import settings
from app.core.exceptions import (
    ConflictError,
    InvalidCredentialsError,
    TokenExpiredError,
    UnauthorizedError,
)
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.auth import LoginRequest, RegisterRequest, TokenPair
from sqlalchemy.ext.asyncio import AsyncSession


class AuthService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.users = UserRepository(db)

    async def register(self, payload: RegisterRequest) -> User:
        if await self.users.get_by_username(payload.username):
            raise ConflictError(code="USERNAME_TAKEN", message="用户名已被占用")
        if await self.users.get_by_email(payload.email):
            raise ConflictError(code="EMAIL_TAKEN", message="邮箱已被占用")

        user = await self.users.create(
            username=payload.username,
            email=payload.email,
            password_hash=hash_password(payload.password),
        )
        await self.db.commit()
        return user

    async def login(self, payload: LoginRequest) -> TokenPair:
        identifier = payload.username or payload.email
        if not identifier:
            raise InvalidCredentialsError("请提供用户名或邮箱")

        user = await self.users.get_by_username_or_email(identifier)
        if user is None or not verify_password(payload.password, user.password_hash):
            raise InvalidCredentialsError()
        return self._issue_tokens(user.id)

    async def refresh(self, refresh_token: str) -> TokenPair:
        try:
            claims = decode_token(refresh_token)
        except jwt.ExpiredSignatureError as exc:
            raise TokenExpiredError() from exc
        except jwt.InvalidTokenError as exc:
            raise UnauthorizedError() from exc

        if claims.get("type") != "refresh":
            raise UnauthorizedError("无效的刷新令牌")

        user = await self.users.get(int(claims["sub"]))
        if user is None:
            raise UnauthorizedError()
        return self._issue_tokens(user.id)

    @staticmethod
    def _issue_tokens(user_id: int) -> TokenPair:
        return TokenPair(
            access_token=create_access_token(user_id),
            refresh_token=create_refresh_token(user_id),
            token_type="bearer",
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )
