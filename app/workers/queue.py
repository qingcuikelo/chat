"""ARQ 队列连接（懒加载单例）与摘要任务投递。"""

import logging

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import settings

logger = logging.getLogger(__name__)

_pool: ArqRedis | None = None


def redis_settings() -> RedisSettings:
    url = settings.ARQ_REDIS_URL or settings.REDIS_URL
    return RedisSettings.from_dsn(url)


async def get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings())
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def enqueue_summary(conversation_id: int) -> None:
    pool = await get_pool()
    await pool.enqueue_job(
        "summarize_conversation",
        conversation_id,
        _job_id=f"summarize:{conversation_id}",
    )


async def enqueue_verification_email(
    to_email: str, token: str, purpose: str = "verify_email"
) -> None:
    pool = await get_pool()
    await pool.enqueue_job("send_verification_email", to_email, token, purpose)


async def enqueue_password_reset_email(to_email: str, token: str) -> None:
    pool = await get_pool()
    await pool.enqueue_job("send_password_reset_email", to_email, token)
