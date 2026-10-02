"""ARQ worker 配置：`arq app.workers.settings.WorkerSettings`。"""

from app.core.logging import setup_logging
from app.workers.queue import redis_settings
from app.workers.tasks import send_verification_email, summarize_conversation


async def on_startup(ctx) -> None:
    setup_logging()


class WorkerSettings:
    functions = [summarize_conversation, send_verification_email]
    redis_settings = redis_settings()
    max_jobs = 10
    job_timeout = 120
    keep_result = 0
    on_startup = on_startup
