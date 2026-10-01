"""Redis 缓存落地测试：实体缓存读写穿透/失效 + 聊天会话上下文缓存。"""

import json
from datetime import datetime

from sqlalchemy import update

from app.models.conversation import Conversation
from app.services.llm_service import LLMService, StreamChunk

API = "/api/v1"


async def test_user_cache_written_and_invalidated(client, auth_headers, fake_redis):
    me = (await client.get(f"{API}/users/me", headers=auth_headers)).json()
    user_id = me["id"]

    cached = await fake_redis.hgetall(f"user:{user_id}")
    assert cached["username"] == "alice"
    assert "password_hash" not in cached

    await client.patch(
        f"{API}/users/me", headers=auth_headers, json={"username": "alice_new"}
    )
    assert await fake_redis.hgetall(f"user:{user_id}") == {}

    await client.get(f"{API}/users/me", headers=auth_headers)
    refreshed = await fake_redis.hgetall(f"user:{user_id}")
    assert refreshed["username"] == "alice_new"


async def test_conversation_cache_and_invalidation(client, auth_headers, fake_redis):
    created = await client.post(
        f"{API}/conversations", headers=auth_headers, json={"title": "缓存"}
    )
    conversation_id = created.json()["id"]

    await client.get(f"{API}/conversations/{conversation_id}", headers=auth_headers)
    cached = await fake_redis.hgetall(f"conversation:{conversation_id}")
    assert cached["title"] == "缓存"

    await client.patch(
        f"{API}/conversations/{conversation_id}",
        headers=auth_headers,
        json={"title": "新标题"},
    )
    assert await fake_redis.hgetall(f"conversation:{conversation_id}") == {}


async def test_chat_session_cache(client, auth_headers, fake_redis, monkeypatch):
    captured: dict = {}

    async def fake_stream(self, messages):
        captured["messages"] = messages
        yield StreamChunk("delta", text="好的")
        yield StreamChunk("final", finish_reason="stop")

    monkeypatch.setattr(LLMService, "stream_chat", fake_stream)

    conversation_id = (
        await client.post(
            f"{API}/conversations", headers=auth_headers, json={"title": "会话"}
        )
    ).json()["id"]

    await client.post(
        f"{API}/chat",
        headers=auth_headers,
        json={"conversation_id": conversation_id, "message": "你好"},
    )
    first = await fake_redis.lrange(f"chat:session:{conversation_id}", 0, -1)
    assert [json.loads(item)["role"] for item in first] == ["user", "assistant"]

    await client.post(
        f"{API}/chat",
        headers=auth_headers,
        json={"conversation_id": conversation_id, "message": "继续"},
    )
    roles = [message["role"] for message in captured["messages"]]
    assert roles[0] == "system"
    assert "user" in roles and "assistant" in roles

    second = await fake_redis.lrange(f"chat:session:{conversation_id}", 0, -1)
    assert len(second) == 4


async def test_clearing_session_on_conversation_delete(
    client, auth_headers, fake_redis, monkeypatch
):
    async def fake_stream(self, messages):
        yield StreamChunk("delta", text="ok")
        yield StreamChunk("final", finish_reason="stop")

    monkeypatch.setattr(LLMService, "stream_chat", fake_stream)

    conversation_id = (
        await client.post(
            f"{API}/conversations", headers=auth_headers, json={"title": "临时"}
        )
    ).json()["id"]
    await client.post(
        f"{API}/chat",
        headers=auth_headers,
        json={"conversation_id": conversation_id, "message": "hi"},
    )
    assert await fake_redis.exists(f"chat:session:{conversation_id}")

    await client.delete(f"{API}/conversations/{conversation_id}", headers=auth_headers)
    assert not await fake_redis.exists(f"chat:session:{conversation_id}")


async def test_chat_bumps_conversation_activity(
    client, auth_headers, fake_redis, session_factory, monkeypatch
):
    async def fake_stream(self, messages):
        yield StreamChunk("delta", text="ok")
        yield StreamChunk("final", finish_reason="stop")

    monkeypatch.setattr(LLMService, "stream_chat", fake_stream)

    conversation_id = (
        await client.post(
            f"{API}/conversations", headers=auth_headers, json={"title": "活跃度"}
        )
    ).json()["id"]

    await client.get(f"{API}/conversations/{conversation_id}", headers=auth_headers)
    assert await fake_redis.hgetall(f"conversation:{conversation_id}")

    old = datetime(2000, 1, 1)
    async with session_factory() as session:
        await session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(updated_at=old)
        )
        await session.commit()

    await client.post(
        f"{API}/chat",
        headers=auth_headers,
        json={"conversation_id": conversation_id, "message": "hi"},
    )

    assert await fake_redis.hgetall(f"conversation:{conversation_id}") == {}

    async with session_factory() as session:
        refreshed = await session.get(Conversation, conversation_id)
        assert refreshed.updated_at > old


async def test_active_conversation_sorted_first(client, auth_headers, monkeypatch):
    async def fake_stream(self, messages):
        yield StreamChunk("delta", text="ok")
        yield StreamChunk("final", finish_reason="stop")

    monkeypatch.setattr(LLMService, "stream_chat", fake_stream)

    first = (
        await client.post(
            f"{API}/conversations", headers=auth_headers, json={"title": "A"}
        )
    ).json()["id"]
    second = (
        await client.post(
            f"{API}/conversations", headers=auth_headers, json={"title": "B"}
        )
    ).json()["id"]

    await client.post(
        f"{API}/chat",
        headers=auth_headers,
        json={"conversation_id": first, "message": "hi"},
    )

    items = (await client.get(f"{API}/conversations", headers=auth_headers)).json()[
        "items"
    ]
    assert items[0]["id"] == first
    assert items[1]["id"] == second
