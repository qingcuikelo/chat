"""认证令牌状态：access 黑名单 + refresh 白名单（Redis）。

- `auth:blacklist:{jti}`  String  TTL=access 剩余有效期  已登出的 access 令牌
- `auth:refresh:{jti}`    String  TTL=refresh 有效期    可用 refresh 令牌（支持撤销与轮换）

Redis 不可用时按 fail-open 处理（放行并告警），保证鉴权可用性。
"""

import logging
from typing import Any

from app.core.config import settings
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

BLACKLIST_KEY = "auth:blacklist:{jti}"
REFRESH_KEY = "auth:refresh:{jti}"
PASSWORD_RESET_KEY = "auth:pwdreset:{jti}"
LOCKOUT_KEY = "auth:lockout:{identifier}:{ip}"


class TokenService:
    def __init__(self, redis: Any | None = None) -> None:
        self._redis = redis

    @property
    def redis(self):
        return self._redis or get_redis()

    # ---- access 黑名单 ----
    async def blacklist_access(self, jti: str, ttl: int, *, raise_on_failure: bool = False) -> None:
        if ttl <= 0:
            return
        try:
            await self.redis.set(BLACKLIST_KEY.format(jti=jti), "1", ex=ttl)
        except Exception:
            logger.warning("写入 access 黑名单失败 jti=%s", jti, exc_info=True)
            if raise_on_failure:
                raise

    async def is_blacklisted(self, jti: str) -> bool:
        try:
            return bool(await self.redis.exists(BLACKLIST_KEY.format(jti=jti)))
        except Exception:
            logger.warning("读取 access 黑名单失败，fail-open 放行 jti=%s", jti, exc_info=True)
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

    async def consume_refresh(self, jti: str) -> int | None:
        """原子消费 refresh 令牌；Redis 故障时抛出异常由调用方 fail-closed。"""
        key = REFRESH_KEY.format(jti=jti)
        try:
            value = await self.redis.getdel(key)
        except Exception:
            try:
                value = await self.redis.get(key)
                if value is not None:
                    await self.redis.delete(key)
            except Exception:
                logger.warning("消费 refresh 令牌失败 jti=%s", jti, exc_info=True)
                raise
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    async def revoke_refresh(self, jti: str) -> None:
        try:
            await self.redis.delete(REFRESH_KEY.format(jti=jti))
        except Exception:
            logger.warning("删除 refresh 白名单失败 jti=%s", jti, exc_info=True)

    async def revoke_user_refresh_tokens(
        self, user_id: int, *, raise_on_failure: bool = False
    ) -> None:
        """撤销指定用户的全部 refresh 令牌（重新登录后使用）。"""
        try:
            async for key in self.redis.scan_iter(match="auth:refresh:*"):
                value = await self.redis.get(key)
                if value == str(user_id):
                    await self.redis.delete(key)
        except Exception:
            logger.warning("撤销用户 refresh 令牌失败 user_id=%s", user_id, exc_info=True)
            if raise_on_failure:
                raise

    # ---- 密码重置一次性令牌 ----
    async def store_password_reset(self, jti: str, user_id: int, ttl: int) -> None:
        try:
            key = PASSWORD_RESET_KEY.format(jti=jti)
            if ttl > 0:
                await self.redis.set(key, str(user_id), ex=ttl)
            else:
                await self.redis.set(key, str(user_id))
        except Exception:
            logger.warning("写入密码重置令牌失败 jti=%s", jti, exc_info=True)

    async def consume_password_reset(self, jti: str) -> int | None:
        """原子消费一次性重置令牌；返回 user_id，失败/已使用返回 None。"""
        key = PASSWORD_RESET_KEY.format(jti=jti)
        try:
            try:
                value = await self.redis.getdel(key)
            except Exception:
                value = await self.redis.get(key)
                if value is not None:
                    await self.redis.delete(key)
        except Exception:
            logger.warning("消费密码重置令牌失败 jti=%s", jti, exc_info=True)
            return None
        return int(value) if value is not None else None

    # ---- 登录失败锁定 ----
    @staticmethod
    def _lockout_key(identifier: str, ip: str) -> str:
        return LOCKOUT_KEY.format(identifier=identifier.strip().lower(), ip=ip.strip().lower())

    async def login_failure_count(self, identifier: str, ip: str) -> int:
        try:
            value = await self.redis.get(self._lockout_key(identifier, ip))
        except Exception:
            logger.warning("读取登录失败计数失败，fail-open", exc_info=True)
            return 0
        return int(value or 0)

    async def register_login_failure(self, identifier: str, ip: str) -> int:
        try:
            key = self._lockout_key(identifier, ip)
            pipe = self.redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, settings.AUTH_LOCKOUT_MINUTES * 60)
            result = await pipe.execute()
            return int(result[0] or 0)
        except Exception:
            logger.warning("写入登录失败计数失败，fail-open", exc_info=True)
            return 0

    async def clear_login_failures(self, identifier: str, ip: str) -> None:
        try:
            await self.redis.delete(self._lockout_key(identifier, ip))
        except Exception:
            logger.warning("清理登录失败计数失败", exc_info=True)
