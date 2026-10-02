"""后台任务：会话滚动摘要。"""

import logging

from app.core.context import conversation_id_var
from app.core.database import AsyncSessionLocal
from app.core.redis import get_redis
from app.services.cache_service import CacheService
from app.services.mail_service import MailService
from app.services.summary_service import SummaryService

logger = logging.getLogger(__name__)

LOCK_KEY = "lock:summary:{conversation_id}"
LOCK_TTL_SECONDS = 60


async def send_verification_email(ctx, to_email: str, token: str) -> None:
    await MailService().send_verification_email(to_email, token)


async def summarize_conversation(ctx, conversation_id: int) -> None:
    conversation_id_var.set(str(conversation_id))
    redis = get_redis()
    lock_key = LOCK_KEY.format(conversation_id=conversation_id)
    acquired = await redis.set(lock_key, "1", nx=True, ex=LOCK_TTL_SECONDS)
    if not acquired:
        return
    try:
        async with AsyncSessionLocal() as session:
            await SummaryService(session).compress(conversation_id)
        await CacheService().invalidate_conversation(conversation_id)
    except Exception:
        logger.warning(
            "后台摘要任务失败 conversation_id=%s", conversation_id, exc_info=True
        )
    finally:
        try:
            await redis.delete(lock_key)
        except Exception:
            pass
