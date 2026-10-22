"""基于 Redis 的原子滑动窗口限流与客户端 IP 解析。"""

import ipaddress
import logging
import time
import uuid
from functools import lru_cache

from app.core.config import settings
from app.core.exceptions import RateLimitedError
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

RATE_LIMIT_WINDOW_SECONDS = 60

# KEYS[1]=key ARGV[1]=now_ms ARGV[2]=window_ms ARGV[3]=limit ARGV[4]=unique member
_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
  return 0
end
redis.call('ZADD', key, now, ARGV[4])
redis.call('PEXPIRE', key, window)
return 1
"""


def _get_script(redis):
    # 脚本对象按 Redis 客户端缓存，避免跨实例复用；redis-py 自动 NOSCRIPT 回退
    script = getattr(redis, "_sliding_window_script", None)
    if script is None:
        script = redis.register_script(_SLIDING_WINDOW_LUA)
        try:
            redis._sliding_window_script = script
        except Exception:
            pass
    return script


async def _allow_request(key: str, limit: int, window_seconds: int) -> bool:
    redis = get_redis()
    script = _get_script(redis)
    now_ms = int(time.time() * 1000)
    member = f"{now_ms}-{uuid.uuid4().hex}"
    result = await script(
        keys=[key],
        args=[now_ms, window_seconds * 1000, limit, member],
    )
    return bool(result)


async def check_rate_limit(
    key: str, limit: int, *, window_seconds: int = RATE_LIMIT_WINDOW_SECONDS
) -> None:
    """超过阈值抛 RateLimitedError（带 Retry-After）；Redis 故障按 fail_mode 处理。"""
    if limit <= 0:
        return
    try:
        allowed = await _allow_request(key, limit, window_seconds)
    except Exception as exc:
        if settings.RATE_LIMIT_FAIL_MODE.lower() == "closed":
            raise RateLimitedError(headers={"Retry-After": str(window_seconds)}) from exc
        logger.warning("限流依赖不可用，fail-open 放行 key=%s", key, exc_info=True)
        return
    if not allowed:
        raise RateLimitedError(headers={"Retry-After": str(window_seconds)})


@lru_cache
def _parse_networks(value: str) -> tuple[ipaddress._BaseNetwork, ...]:
    networks = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            logger.warning("非法的 TRUSTED_PROXIES 项：%s", item)
    return tuple(networks)


def _ip_in_trusted(ip: str, networks) -> bool:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(address in network for network in networks)


def get_client_ip(request) -> str:
    """获取客户端 IP：默认直连地址；仅可信代理直连时才解析转发头。"""
    direct = request.client.host if request.client else "unknown"
    if not settings.RATE_LIMIT_TRUST_FORWARDED_HEADERS:
        return direct

    networks = _parse_networks(settings.TRUSTED_PROXIES)
    if not networks or not _ip_in_trusted(direct, networks):
        return direct

    header = request.headers.get(settings.RATE_LIMIT_CLIENT_IP_HEADER, "")
    candidates = [item.strip() for item in header.split(",") if item.strip()]
    for candidate in reversed(candidates):
        if not _ip_in_trusted(candidate, networks):
            return candidate
    return direct
