"""认证依赖：从 Bearer Token 解析当前用户。"""

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import user_id_var
from app.core.database import get_db
from app.core.exceptions import TokenExpiredError, UnauthorizedError
from app.core.security import decode_token
from app.models.user import User
from app.services.cache_service import CacheService
from app.services.token_service import TokenService

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None or not credentials.credentials:
        raise UnauthorizedError()

    try:
        claims = decode_token(credentials.credentials)
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError() from exc
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError() from exc

    if claims.get("type") != "access":
        raise UnauthorizedError()

    jti = claims.get("jti")
    if jti and await TokenService().is_blacklisted(jti):
        raise UnauthorizedError()

    request.state.access_token_claims = claims
    user_id = int(claims["sub"])
    user_id_var.set(str(user_id))

    cache = CacheService()
    cached_user = await cache.get_user(user_id)
    if cached_user is not None:
        # 用户缓存可能被并发旧请求回填，token_version 必须以数据库为准。
        authoritative_version = await db.scalar(
            select(User.token_version).where(User.id == user_id)
        )
        if authoritative_version is None or int(claims.get("ver", 0)) != int(authoritative_version):
            raise UnauthorizedError()
        return cached_user

    user = await db.get(User, user_id)
    if user is None or int(claims.get("ver", 0)) != int(user.token_version or 0):
        raise UnauthorizedError()

    await cache.set_user(user)
    return user
