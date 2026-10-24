"""用户 token 用量与配额服务（v3 WP6）。"""

import logging
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import utcnow
from app.core.exceptions import QuotaExceededError
from app.core.redis import get_redis
from app.models.usage import UsageDaily
from app.repositories.usage_repository import UsageRepository

logger = logging.getLogger(__name__)

QUOTA_KEY = "quota:{user_id}:{yyyymmdd}"


def quota_key(user_id: int, usage_date: date) -> str:
    return QUOTA_KEY.format(user_id=user_id, yyyymmdd=usage_date.strftime("%Y%m%d"))


def quota_ttl_seconds(usage_date: date) -> int:
    """返回距离该 UTC 日结束的秒数（至少 60 秒）。"""
    end = datetime.combine(usage_date + timedelta(days=1), dt_time.min)
    return max(60, int((end - utcnow()).total_seconds()))


class UsageService:
    def __init__(self, db: AsyncSession | None = None) -> None:
        self.db = db
        self.repo = UsageRepository(db) if db is not None else None

    async def record_turn(
        self,
        *,
        user_id: int,
        usage_date: date,
        prompt_tokens: int,
        completion_tokens: int,
        request_count: int = 1,
    ) -> None:
        if self.repo is None:
            raise RuntimeError("record_turn 需要数据库会话")
        await self.repo.upsert(
            user_id=user_id,
            usage_date=usage_date,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            request_count=request_count,
        )

    async def usage_range(self, user_id: int, start_date: date, end_date: date) -> list[UsageDaily]:
        if self.repo is None:
            raise RuntimeError("usage_range 需要数据库会话")
        return await self.repo.list_range(user_id, start_date, end_date)

    async def totals_for_date(self, user_id: int, usage_date: date) -> tuple[int, int]:
        if self.repo is None:
            raise RuntimeError("totals_for_date 需要数据库会话")
        return await self.repo.totals_for_date(user_id, usage_date)

    async def add_redis_tokens(self, user_id: int, usage_date: date, tokens: int) -> None:
        """成功后累加 Redis 快计数；失败只告警，DB 已记录。"""
        if tokens <= 0:
            return
        try:
            pipe = get_redis().pipeline()
            key = quota_key(user_id, usage_date)
            pipe.incrby(key, tokens)
            pipe.expire(key, quota_ttl_seconds(usage_date))
            await pipe.execute()
        except Exception:
            logger.warning("写入 Redis 配额计数失败 user_id=%s", user_id, exc_info=True)

    async def _redis_used(self, user_id: int, usage_date: date) -> int | None:
        try:
            value = await get_redis().get(quota_key(user_id, usage_date))
        except Exception:
            logger.warning(
                "读取 Redis 配额计数失败，fail-open user_id=%s",
                user_id,
                exc_info=True,
            )
            return None
        return int(value or 0)

    async def _seed_redis(self, user_id: int, usage_date: date, tokens: int) -> None:
        if tokens <= 0:
            return
        try:
            await get_redis().set(
                quota_key(user_id, usage_date),
                tokens,
                ex=quota_ttl_seconds(usage_date),
                nx=True,
            )
        except Exception:
            logger.warning("回填 Redis 配额计数失败 user_id=%s", user_id, exc_info=True)

    async def ensure_within_quota(self, user_id: int) -> None:
        limit = settings.USER_DAILY_TOKEN_QUOTA
        if limit <= 0 or not settings.QUOTA_ENFORCE:
            return

        today = utcnow().date()
        used = await self._redis_used(user_id, today)
        if used is None:
            return
        if used == 0 and self.repo is not None:
            prompt_tokens, completion_tokens = await self.totals_for_date(user_id, today)
            used = prompt_tokens + completion_tokens
            await self._seed_redis(user_id, today, used)
        if used >= limit:
            raise QuotaExceededError(
                headers={
                    "X-Quota-Limit": str(limit),
                    "X-Quota-Used": str(used),
                    "X-Quota-Remaining": "0",
                    "Retry-After": str(quota_ttl_seconds(today)),
                }
            )

    async def quota_state(self, user_id: int) -> dict[str, Any]:
        today = utcnow().date()
        used = await self._redis_used(user_id, today)
        if used in (None, 0):
            prompt_tokens, completion_tokens = await self.totals_for_date(user_id, today)
            db_used = prompt_tokens + completion_tokens
            if db_used:
                used = db_used
                await self._seed_redis(user_id, today, db_used)

        limit = settings.USER_DAILY_TOKEN_QUOTA
        used = int(used or 0)
        remaining = max(0, limit - used) if limit > 0 else None
        return {
            "date": today.isoformat(),
            "limit": limit,
            "used": used,
            "remaining": remaining,
            "enforced": settings.QUOTA_ENFORCE and limit > 0,
        }
