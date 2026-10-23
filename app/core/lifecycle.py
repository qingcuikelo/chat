"""应用生命周期：停机标记、后台生成任务登记与排空。"""

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)

_shutting_down = False
_tasks: set[asyncio.Task] = set()


def is_shutting_down() -> bool:
    return _shutting_down


def begin_shutdown() -> None:
    global _shutting_down
    _shutting_down = True


def reset_shutdown_state() -> None:
    global _shutting_down
    _shutting_down = False


def pending_count() -> int:
    return len(_tasks)


def spawn_generation(coro: Coroutine[Any, Any, Any], *, name: str | None = None) -> asyncio.Task:
    if _shutting_down:
        raise RuntimeError("应用正在停机，拒绝新的生成任务")
    task = asyncio.create_task(coro, name=name)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


async def drain_generations(timeout: float) -> int:
    """等待在途任务完成；超时后取消，返回被取消的任务数。"""
    pending = set(_tasks)
    if not pending:
        return 0

    if timeout > 0:
        _, pending = await asyncio.wait(pending, timeout=timeout)
    cancelled = len(pending)
    if pending:
        logger.warning("停机排空超时，取消在途生成 tasks=%s", cancelled)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    return cancelled


async def cancel_pending() -> None:
    """测试/兜底：取消所有登记任务。"""
    pending = set(_tasks)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    _tasks.clear()
