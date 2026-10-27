"""聊天并发槽位测试。"""

import app.api.v1.chat as chat_module
from app.core.concurrency import acquire_chat_slot, release_chat_slot
from app.core.config import settings

API = "/api/v1"


async def test_chat_slots_limit_per_user(fake_redis, monkeypatch):
    monkeypatch.setattr(settings, "CHAT_MAX_CONCURRENT_PER_USER", 1)
    monkeypatch.setattr(settings, "CHAT_MAX_CONCURRENT_TOTAL", 10)

    assert await acquire_chat_slot(1) == (True, True)
    assert await acquire_chat_slot(1) == (False, False)
    assert await acquire_chat_slot(2) == (True, True)

    await release_chat_slot(1)
    assert await acquire_chat_slot(1) == (True, True)

    await release_chat_slot(1)
    await release_chat_slot(2)


async def test_chat_slots_limit_total(fake_redis, monkeypatch):
    monkeypatch.setattr(settings, "CHAT_MAX_CONCURRENT_PER_USER", 10)
    monkeypatch.setattr(settings, "CHAT_MAX_CONCURRENT_TOTAL", 1)

    assert await acquire_chat_slot(1) == (True, True)
    assert await acquire_chat_slot(2) == (False, False)

    await release_chat_slot(1)
    assert await acquire_chat_slot(2) == (True, True)
    await release_chat_slot(2)


async def test_chat_slot_fail_open_is_not_counted(fake_redis, monkeypatch):
    def unavailable(*args, **kwargs):
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(fake_redis, "register_script", unavailable)

    assert await acquire_chat_slot(1) == (True, False)


async def test_chat_endpoint_returns_429_when_slot_unavailable(client, auth_headers, monkeypatch):
    conversation_id = (
        await client.post(f"{API}/conversations", headers=auth_headers, json={})
    ).json()["id"]

    async def deny(user_id: int) -> tuple[bool, bool]:
        return False, False

    monkeypatch.setattr(chat_module, "acquire_chat_slot", deny)
    response = await client.post(
        f"{API}/chat",
        headers=auth_headers,
        json={"conversation_id": conversation_id, "message": "hi"},
    )

    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "RATE_LIMITED"
