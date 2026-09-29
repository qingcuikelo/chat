"""认证依赖：从 Bearer Token 解析当前用户。"""

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import TokenExpiredError, UnauthorizedError
from app.core.security import decode_token
from app.models.user import User
from app.services.cache_service import CacheService

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
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

    user_id = int(claims["sub"])

    cache = CacheService()
    cached_user = await cache.get_user(user_id)
    if cached_user is not None:
        return cached_user

    user = await db.get(User, user_id)
    if user is None:
        raise UnauthorizedError()

    await cache.set_user(user)
    return user
