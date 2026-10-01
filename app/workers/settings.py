"""ARQ worker 配置：`arq app.workers.settings.WorkerSettings`。"""

from app.workers.queue import redis_settings
from app.workers.tasks import summarize_conversation


class WorkerSettings:
    functions = [summarize_conversation]
    redis_settings = redis_settings()
    max_jobs = 10
    job_timeout = 120
    keep_result = 0
