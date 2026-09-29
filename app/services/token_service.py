"""认证令牌状态：access 黑名单 + refresh 白名单（Redis）。

- `auth:blacklist:{jti}`  String  TTL=access 剩余有效期  已登出的 access 令牌
- `auth:refresh:{jti}`    String  TTL=refresh 有效期    可用 refresh 令牌（支持撤销与轮换）

Redis 不可用时按 fail-open 处理（放行并告警），保证鉴权可用性。
"""

import logging
from typing import Any

from app.core.redis import get_redis

logger = logging.getLogger(__name__)

BLACKLIST_KEY = "auth:blacklist:{jti}"
REFRESH_KEY = "auth:refresh:{jti}"


class TokenService:
    def __init__(self, redis: Any | None = None) -> None:
        self._redis = redis

    @property
    def redis(self):
        return self._redis or get_redis()

    # ---- access 黑名单 ----
    async def blacklist_access(self, jti: str, ttl: int) -> None:
        if ttl <= 0:
            return
        try:
            await self.redis.set(BLACKLIST_KEY.format(jti=jti), "1", ex=ttl)
        except Exception:
            logger.warning("写入 access 黑名单失败 jti=%s", jti, exc_info=True)

    async def is_blacklisted(self, jti: str) -> bool:
        try:
            return bool(await self.redis.exists(BLACKLIST_KEY.format(jti=jti)))
        except Exception:
            logger.warning(
                "读取 access 黑名单失败，fail-open 放行 jti=%s", jti, exc_info=True
            )
            return False

    # ---- refresh 白名单 ----
    async def store_refresh(self, jti: str, user_id: int, ttl: int) -> None:
        try:
            key = REFRESH_KEY.format(jti=jti)
            if ttl > 0:
                await self.redis.set(key, str(user_id), ex=ttl)
            else:
                await self.redis.set(key, str(user_id))
        except Exception:
            logger.warning("写入 refresh 白名单失败 jti=%s", jti, exc_info=True)

    async def is_refresh_active(self, jti: str) -> bool | None:
        """True=有效；False=已失效/已用；None=Redis 故障（fail-open）。"""
        try:
            return bool(await self.redis.exists(REFRESH_KEY.format(jti=jti)))
        except Exception:
            logger.warning(
                "读取 refresh 白名单失败，fail-open 放行 jti=%s", jti, exc_info=True
            )
            return None

    async def revoke_refresh(self, jti: str) -> None:
        try:
            await self.redis.delete(REFRESH_KEY.format(jti=jti))
        except Exception:
            logger.warning("删除 refresh 白名单失败 jti=%s", jti, exc_info=True)
