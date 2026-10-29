"""后台任务：会话滚动摘要、消息归档。"""

import logging
from datetime import timedelta

from sqlalchemy import delete, insert, select

from app.core.config import settings
from app.core.context import conversation_id_var
from app.core.database import AsyncSessionLocal, utcnow
from app.core.redis import get_redis
from app.models.message import Message
from app.models.message_archive import MessageArchive
from app.models.usage import UsageDaily
from app.services.cache_service import CacheService
from app.services.mail_service import MailService
from app.services.summary_service import SummaryService

logger = logging.getLogger(__name__)

LOCK_KEY = "lock:summary:{conversation_id}"
LOCK_TTL_SECONDS = 60

ARCHIVE_LOCK_KEY = "lock:archive:messages"
ARCHIVE_LOCK_TTL_SECONDS = 3600

ARCHIVE_COLUMNS = [
    "id",
    "conversation_id",
    "role",
    "content",
    "token_count",
    "created_at",
    "metadata",
]


async def send_verification_email(
    ctx, to_email: str, token: str, purpose: str = "verify_email"
) -> None:
    await MailService().send_verification_email(to_email, token, purpose)


async def send_password_reset_email(ctx, to_email: str, token: str) -> None:
    await MailService().send_password_reset_email(to_email, token)


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
        logger.warning("后台摘要任务失败 conversation_id=%s", conversation_id, exc_info=True)
    finally:
        try:
            await redis.delete(lock_key)
        except Exception:
            pass


async def archive_messages(
    ctx=None, *, batch_size: int | None = None, after_days: int | None = None
) -> dict[str, int]:
    """将超过保留期的消息分批迁移到 `messages_archive`（v3 WP8b）。"""
    if not settings.MESSAGE_ARCHIVE_ENABLED:
        return {"archived": 0}

    days = settings.MESSAGE_ARCHIVE_AFTER_DAYS if after_days is None else after_days
    size = settings.MESSAGE_ARCHIVE_BATCH_SIZE if batch_size is None else batch_size
    if days < 0 or size <= 0:
        return {"archived": 0}

    cutoff = utcnow() - timedelta(days=days)
    redis = get_redis()
    acquired = await redis.set(ARCHIVE_LOCK_KEY, "1", nx=True, ex=ARCHIVE_LOCK_TTL_SECONDS)
    if not acquired:
        logger.info("归档任务已在运行，跳过本次执行")
        return {"archived": 0}

    total = 0
    try:
        while True:
            async with AsyncSessionLocal() as session:
                ids = list(
                    (
                        await session.execute(
                            select(Message.id)
                            .where(Message.created_at < cutoff)
                            .order_by(Message.created_at, Message.id)
                            .limit(size)
                            .with_for_update()
                        )
                    ).scalars()
                )
                if not ids:
                    break

                await session.execute(
                    insert(MessageArchive).from_select(
                        ARCHIVE_COLUMNS,
                        select(
                            Message.id,
                            Message.conversation_id,
                            Message.role,
                            Message.content,
                            Message.token_count,
                            Message.created_at,
                            Message.metadata_,
                        ).where(Message.id.in_(ids)),
                    )
                )
                await session.execute(delete(Message).where(Message.id.in_(ids)))
                await session.commit()

            total += len(ids)
            if len(ids) < size:
                break
    except Exception:
        logger.warning("消息归档任务失败", exc_info=True)
        raise
    finally:
        try:
            await redis.delete(ARCHIVE_LOCK_KEY)
        except Exception:
            pass

    if total:
        logger.info("消息归档完成 archived=%s cutoff=%s", total, cutoff)
    return {"archived": total}


async def cleanup_usage(ctx=None, *, retention_days: int | None = None) -> dict[str, int]:
    """按 USAGE_RETENTION_DAYS 清理过期 usage_daily（v3 WP6）。"""
    days = settings.USAGE_RETENTION_DAYS if retention_days is None else retention_days
    if days <= 0:
        return {"deleted": 0}

    cutoff = utcnow().date() - timedelta(days=days)
    redis = get_redis()
    acquired = await redis.set("lock:cleanup:usage", "1", nx=True, ex=3600)
    if not acquired:
        return {"deleted": 0}

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(delete(UsageDaily).where(UsageDaily.usage_date < cutoff))
            await session.commit()
            deleted = int(getattr(result, "rowcount", 0) or 0)
    finally:
        try:
            await redis.delete("lock:cleanup:usage")
        except Exception:
            pass

    if deleted:
        logger.info("用量清理完成 deleted=%s cutoff=%s", deleted, cutoff)
    return {"deleted": deleted}
