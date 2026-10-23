"""ARQ worker 配置：`arq app.workers.settings.WorkerSettings`。"""

from arq import cron

from app.core.config import settings
from app.core.logging import setup_logging
from app.workers.queue import redis_settings
from app.workers.tasks import (
    archive_messages,
    send_password_reset_email,
    send_verification_email,
    summarize_conversation,
)
from app.workers.write_consumer import start_consumer, stop_consumer


async def on_startup(ctx) -> None:
    setup_logging()
    if settings.WRITE_QUEUE_ENABLED:
        ctx["write_consumer_task"] = await start_consumer()


async def on_shutdown(ctx) -> None:
    await stop_consumer(ctx.get("write_consumer_task"))


class WorkerSettings:
    functions = [
        summarize_conversation,
        send_verification_email,
        send_password_reset_email,
    ]
    cron_jobs = [cron(archive_messages, hour=3, minute=0, timeout=3600)]
    redis_settings = redis_settings()
    max_jobs = 10
    job_timeout = 120
    keep_result = 0
    health_check_key = "worker:heartbeat"
    health_check_interval = 30
    on_startup = on_startup
    on_shutdown = on_shutdown
