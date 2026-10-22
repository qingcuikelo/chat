"""v2 WP5 限流测试。"""

from app.core import rate_limit
from app.core.config import settings
from app.core.rate_limit import get_client_ip

API = "/api/v1"


async def test_login_rate_limited_returns_429(client, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_LOGIN_PER_MINUTE", 1)
    monkeypatch.setattr(settings, "RATE_LIMIT_LOGIN_IP_PER_MINUTE", 1000)

    payload = {"username": "nobody", "password": "whatever"}
    first = await client.post(f"{API}/auth/login", json=payload)
    assert first.status_code == 401

    second = await client.post(f"{API}/auth/login", json=payload)
    assert second.status_code == 429
    assert second.json()["detail"]["code"] == "RATE_LIMITED"
    assert second.headers.get("retry-after") == "60"


async def test_rate_limit_fail_open(client, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr(rate_limit, "_allow_request", boom)
    monkeypatch.setattr(settings, "RATE_LIMIT_FAIL_MODE", "open")

    # 放行后走到鉴权逻辑，返回 401（而非 429/500）
    response = await client.post(f"{API}/auth/login", json={"username": "x", "password": "y"})
    assert response.status_code == 401


async def test_rate_limit_fail_closed(client, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr(rate_limit, "_allow_request", boom)
    monkeypatch.setattr(settings, "RATE_LIMIT_FAIL_MODE", "closed")

    response = await client.post(f"{API}/auth/login", json={"username": "x", "password": "y"})
    assert response.status_code == 429


def test_get_client_ip_trusted_proxy(monkeypatch):
    rate_limit._parse_networks.cache_clear()
    monkeypatch.setattr(settings, "RATE_LIMIT_TRUST_FORWARDED_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "10.0.0.0/8")

    class _Client:
        host = "10.1.2.3"

    class _Request:
        client = _Client()
        headers = {"X-Forwarded-For": "203.0.113.9, 10.0.0.5"}

    assert get_client_ip(_Request()) == "203.0.113.9"

    # 直连非可信地址：忽略伪造转发头
    class _Client2:
        host = "203.0.113.1"

    class _Request2:
        client = _Client2()
        headers = {"X-Forwarded-For": "1.2.3.4"}

    assert get_client_ip(_Request2()) == "203.0.113.1"
