"""认证业务逻辑。"""

import jwt

from app.core.config import settings
from app.core.database import utcnow
from app.core.exceptions import (
    ConflictError,
    EmailNotVerifiedError,
    InvalidCredentialsError,
    InvalidRequestError,
    TokenExpiredError,
    UnauthorizedError,
)
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    remaining_ttl,
    verify_password,
)
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.auth import LoginRequest, RegisterRequest, TokenPair
from app.services.cache_service import CacheService
from app.services.mail_service import dispatch_verification_email
from app.services.token_service import TokenService
from sqlalchemy.ext.asyncio import AsyncSession


class AuthService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.users = UserRepository(db)
        self.tokens = TokenService()
        self.cache = CacheService()

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
        if not settings.EMAIL_VERIFICATION_REQUIRED:
            user.email_verified = True
            user.email_verified_at = utcnow()
        await self.db.commit()

        if settings.EMAIL_VERIFICATION_REQUIRED:
            await dispatch_verification_email(user)
        return user

    async def login(self, payload: LoginRequest) -> TokenPair:
        identifier = payload.username or payload.email
        if not identifier:
            raise InvalidCredentialsError("请提供用户名或邮箱")

        user = await self.users.get_by_username_or_email(identifier)
        if user is None or not verify_password(payload.password, user.password_hash):
            raise InvalidCredentialsError()
        if settings.EMAIL_VERIFICATION_REQUIRED and not user.email_verified:
            raise EmailNotVerifiedError()
        return await self._issue_tokens(user.id)

    async def verify_email(self, token: str) -> User:
        try:
            claims = decode_token(token)
        except jwt.ExpiredSignatureError as exc:
            raise InvalidRequestError("验证链接已过期") from exc
        except jwt.InvalidTokenError as exc:
            raise InvalidRequestError("验证链接无效") from exc

        if claims.get("type") != "email_verify":
            raise InvalidRequestError("验证链接无效")

        user = await self.users.get(int(claims["sub"]))
        if user is None or user.email != claims.get("email"):
            raise InvalidRequestError("验证链接无效")

        if not user.email_verified:
            user.email_verified = True
            user.email_verified_at = utcnow()
            await self.db.commit()
            await self.cache.invalidate_user(user.id)
        return user

    async def resend_verification(self, email: str) -> None:
        """始终静默处理，避免账号枚举（是否发送由内部判断）。"""
        user = await self.users.get_by_email(email)
        if user is None or user.email_verified:
            return
        await dispatch_verification_email(user)

    async def refresh(self, refresh_token: str) -> TokenPair:
        claims = self._decode(refresh_token)
        if claims.get("type") != "refresh":
            raise UnauthorizedError("无效的刷新令牌")

        jti = claims.get("jti")
        if not jti:
            raise UnauthorizedError("无效的刷新令牌")

        active = await self.tokens.is_refresh_active(jti)
        if active is False:
            raise UnauthorizedError("刷新令牌已失效")

        user = await self.users.get(int(claims["sub"]))
        if user is None:
            raise UnauthorizedError()

        # 轮换：旧 refresh 一次性作废
        await self.tokens.revoke_refresh(jti)
        return await self._issue_tokens(user.id)

    async def logout(self, access_token: str, refresh_token: str | None = None) -> None:
        """作废当前 access 令牌；如提供 refresh，则一并作废。"""
        claims = self._decode(access_token)
        jti = claims.get("jti")
        if jti:
            await self.tokens.blacklist_access(jti, remaining_ttl(claims))

        if refresh_token:
            try:
                refresh_claims = decode_token(refresh_token)
            except jwt.InvalidTokenError:
                refresh_claims = None
            if (
                refresh_claims
                and refresh_claims.get("type") == "refresh"
                and refresh_claims.get("jti")
            ):
                await self.tokens.revoke_refresh(refresh_claims["jti"])

    @staticmethod
    def _decode(token: str) -> dict:
        try:
            return decode_token(token)
        except jwt.ExpiredSignatureError as exc:
            raise TokenExpiredError() from exc
        except jwt.InvalidTokenError as exc:
            raise UnauthorizedError() from exc

    async def _issue_tokens(self, user_id: int) -> TokenPair:
        access_token = create_access_token(user_id)
        refresh_token = create_refresh_token(user_id)
        refresh_claims = decode_token(refresh_token)
        await self.tokens.store_refresh(
            refresh_claims["jti"], user_id, remaining_ttl(refresh_claims)
        )
        return TokenPair(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )
