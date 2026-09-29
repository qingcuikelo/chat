"""基于 Redis 的简单限流（`rate_limit:user:{user_id}`）。"""

from app.core.config import settings
from app.core.exceptions import RateLimitedError
from app.core.redis import get_redis

RATE_LIMIT_WINDOW_SECONDS = 60


async def check_rate_limit(user_id: int) -> None:
    redis = get_redis()
    key = f"rate_limit:user:{user_id}"
    current = await redis.incr(key)
    if current == 1:
        await redis.expire(key, RATE_LIMIT_WINDOW_SECONDS)
    if current > settings.RATE_LIMIT_PER_MINUTE:
        raise RateLimitedError()
