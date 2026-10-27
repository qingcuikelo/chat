"""聊天生成并发槽位（Redis 计数，跨 API worker 生效）。"""

import logging

from app.core.config import settings
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

USER_KEY = "chat:inflight:user:{user_id}"
TOTAL_KEY = "chat:inflight:total"

_ACQUIRE_SCRIPT = """
local user = tonumber(redis.call('GET', KEYS[1]) or '0')
local total = tonumber(redis.call('GET', KEYS[2]) or '0')
if user >= tonumber(ARGV[1]) or total >= tonumber(ARGV[2]) then
  return 0
end
redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], ARGV[3])
redis.call('INCR', KEYS[2])
redis.call('EXPIRE', KEYS[2], ARGV[3])
return 1
"""

_RELEASE_SCRIPT = """
local function decrement(key)
  local value = tonumber(redis.call('GET', key) or '0')
  if value <= 1 then
    redis.call('DEL', key)
  else
    redis.call('DECR', key)
  end
end
decrement(KEYS[1])
decrement(KEYS[2])
return 1
"""


def _script(redis, attr: str, source: str):
    script = getattr(redis, attr, None)
    if script is None:
        script = redis.register_script(source)
        try:
            setattr(redis, attr, script)
        except Exception:
            pass
    return script


async def acquire_chat_slot(user_id: int) -> tuple[bool, bool]:
    """尝试占用一个生成槽位，返回 (是否允许, 是否实际计数)。

    Redis 故障时 fail-open，但第二个值为 False，调用方不得释放槽位。
    """
    per_user = settings.CHAT_MAX_CONCURRENT_PER_USER
    total_limit = settings.CHAT_MAX_CONCURRENT_TOTAL
    if per_user <= 0 and total_limit <= 0:
        return True, False
    if per_user <= 0:
        per_user = 2**31 - 1
    if total_limit <= 0:
        total_limit = 2**31 - 1

    try:
        redis = get_redis()
        script = _script(redis, "_chat_inflight_acquire", _ACQUIRE_SCRIPT)
        result = await script(
            keys=[USER_KEY.format(user_id=user_id), TOTAL_KEY],
            args=[per_user, total_limit, settings.CHAT_INFLIGHT_TTL_SECONDS],
        )
        allowed = bool(result)
        return allowed, allowed
    except Exception:
        logger.warning("获取聊天并发槽失败，fail-open user_id=%s", user_id, exc_info=True)
        return True, False


async def release_chat_slot(user_id: int) -> None:
    """释放生成槽位；重复释放或 Redis 故障不会影响请求结果。"""
    try:
        redis = get_redis()
        script = _script(redis, "_chat_inflight_release", _RELEASE_SCRIPT)
        await script(keys=[USER_KEY.format(user_id=user_id), TOTAL_KEY])
    except Exception:
        logger.warning("释放聊天并发槽失败 user_id=%s", user_id, exc_info=True)
