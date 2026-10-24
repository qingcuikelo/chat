"""v3 WP6 用量与配额测试。"""

from datetime import timedelta

from sqlalchemy import select

import app.workers.tasks as tasks
from app.core.config import settings
from app.core.database import utcnow
from app.models.conversation import Conversation
from app.models.usage import UsageDaily
from app.services.llm_service import LLMService, StreamChunk
from app.services.usage_service import UsageService
from app.services.write_queue import WriteTurn, consume_once, process_turns

API = "/api/v1"


async def _new_conversation(client, headers) -> int:
    response = await client.post(f"{API}/conversations", headers=headers, json={})
    return response.json()["id"]


async def _chat(client, headers, conversation_id: int, message: str = "hi"):
    return await client.post(
        f"{API}/chat",
        headers=headers,
        json={"conversation_id": conversation_id, "message": message},
    )


async def _usage_rows(session_factory):
    async with session_factory() as session:
        return list(
            (await session.execute(select(UsageDaily).order_by(UsageDaily.usage_date))).scalars()
        )


async def test_direct_chat_records_usage(
    client, auth_headers, session_factory, fake_redis, monkeypatch
):
    async def fake_stream(self, messages):
        yield StreamChunk("delta", text="回复")
        yield StreamChunk(
            "final",
            finish_reason="stop",
            usage={"prompt_tokens": 7, "completion_tokens": 2},
        )

    monkeypatch.setattr(LLMService, "stream_chat", fake_stream)

    me = (await client.get(f"{API}/users/me", headers=auth_headers)).json()
    conversation_id = await _new_conversation(client, auth_headers)
    response = await _chat(client, auth_headers, conversation_id)

    assert response.status_code == 200
    rows = await _usage_rows(session_factory)
    assert len(rows) == 1
    assert rows[0].usage_date == utcnow().date()
    assert rows[0].prompt_tokens == 7
    assert rows[0].completion_tokens == 2
    assert rows[0].request_count == 1

    key = f"quota:{me['id']}:{utcnow():%Y%m%d}"
    assert await fake_redis.get(key) == "9"


async def test_prompt_tokens_fallback_when_usage_missing(
    client, auth_headers, session_factory, fake_redis, monkeypatch
):
    async def fake_stream(self, messages):
        yield StreamChunk("delta", text="回复")
        yield StreamChunk("final", finish_reason="stop")

    monkeypatch.setattr(LLMService, "stream_chat", fake_stream)

    conversation_id = await _new_conversation(client, auth_headers)
    await _chat(client, auth_headers, conversation_id)

    rows = await _usage_rows(session_factory)
    assert len(rows) == 1
    assert rows[0].prompt_tokens > 0
    assert rows[0].completion_tokens > 0


async def test_queue_chat_records_usage_after_consume(
    client, auth_headers, session_factory, fake_redis, monkeypatch
):
    async def fake_stream(self, messages):
        yield StreamChunk("delta", text="回复")
        yield StreamChunk(
            "final",
            finish_reason="stop",
            usage={"prompt_tokens": 5, "completion_tokens": 3},
        )

    monkeypatch.setattr(settings, "WRITE_QUEUE_ENABLED", True)
    monkeypatch.setattr(LLMService, "stream_chat", fake_stream)
    await fake_redis.set("chat:write:heartbeat", "1")

    conversation_id = await _new_conversation(client, auth_headers)
    await _chat(client, auth_headers, conversation_id)

    assert await _usage_rows(session_factory) == []

    await consume_once("usage-test", session_factory=session_factory)

    rows = await _usage_rows(session_factory)
    assert len(rows) == 1
    assert rows[0].prompt_tokens == 5
    assert rows[0].completion_tokens == 3
    assert rows[0].request_count == 1


async def test_queue_usage_is_idempotent_on_retry(session_factory, fake_redis):
    async with session_factory() as session:
        conversation = Conversation(user_id=1, title=None)
        session.add(conversation)
        await session.commit()
        conversation_id = conversation.id

    turn = WriteTurn(
        request_id="usage-idem",
        conversation_id=conversation_id,
        user_id=1,
        user_message_id=801,
        assistant_message_id=802,
        user_content="u",
        assistant_content="a",
        user_token_count=1,
        assistant_token_count=3,
        prompt_tokens=5,
        completion_tokens=3,
        usage_date=utcnow().date().isoformat(),
        created_at=utcnow(),
    )

    await process_turns([turn], session_factory)
    await process_turns([turn], session_factory)

    rows = await _usage_rows(session_factory)
    assert len(rows) == 1
    assert rows[0].prompt_tokens == 5
    assert rows[0].completion_tokens == 3
    assert rows[0].request_count == 1


async def test_quota_enforced_before_chat(client, auth_headers, fake_redis, monkeypatch):
    me = (await client.get(f"{API}/users/me", headers=auth_headers)).json()
    conversation_id = await _new_conversation(client, auth_headers)

    monkeypatch.setattr(settings, "USER_DAILY_TOKEN_QUOTA", 10)
    monkeypatch.setattr(settings, "QUOTA_ENFORCE", True)
    await fake_redis.set(f"quota:{me['id']}:{utcnow():%Y%m%d}", "10", ex=3600)

    response = await _chat(client, auth_headers, conversation_id)

    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "QUOTA_EXCEEDED"
    assert response.headers["x-quota-remaining"] == "0"


async def test_quota_disabled_only_records(client, auth_headers, fake_redis, monkeypatch):
    async def fake_stream(self, messages):
        yield StreamChunk("delta", text="回复")
        yield StreamChunk("final", finish_reason="stop")

    monkeypatch.setattr(LLMService, "stream_chat", fake_stream)
    monkeypatch.setattr(settings, "USER_DAILY_TOKEN_QUOTA", 1)

    me = (await client.get(f"{API}/users/me", headers=auth_headers)).json()
    conversation_id = await _new_conversation(client, auth_headers)
    await fake_redis.set(f"quota:{me['id']}:{utcnow():%Y%m%d}", "999", ex=3600)

    response = await _chat(client, auth_headers, conversation_id)

    assert response.status_code == 200


async def test_usage_and_quota_endpoints(
    client, auth_headers, session_factory, fake_redis, monkeypatch
):
    async def fake_stream(self, messages):
        yield StreamChunk("delta", text="回复")
        yield StreamChunk(
            "final",
            finish_reason="stop",
            usage={"prompt_tokens": 4, "completion_tokens": 6},
        )

    monkeypatch.setattr(LLMService, "stream_chat", fake_stream)
    monkeypatch.setattr(settings, "USER_DAILY_TOKEN_QUOTA", 100)

    me = (await client.get(f"{API}/users/me", headers=auth_headers)).json()
    conversation_id = await _new_conversation(client, auth_headers)
    await _chat(client, auth_headers, conversation_id)

    today = utcnow().date()
    usage = await client.get(
        f"{API}/users/me/usage",
        headers=auth_headers,
        params={"from": today.isoformat(), "to": today.isoformat()},
    )
    assert usage.status_code == 200
    body = usage.json()
    assert body["from"] == today.isoformat()
    assert body["to"] == today.isoformat()
    assert body["items"][0]["date"] == today.isoformat()
    assert body["total_tokens"] == 10

    await fake_redis.delete(f"quota:{me['id']}:{today:%Y%m%d}")
    quota = await client.get(f"{API}/users/me/quota", headers=auth_headers)
    assert quota.status_code == 200
    quota_body = quota.json()
    assert quota_body["date"] == today.isoformat()
    assert quota_body["used"] == 10
    assert quota_body["remaining"] == 90


async def test_quota_falls_back_to_db_after_redis_reset(
    client, auth_headers, session_factory, fake_redis, monkeypatch
):
    me = (await client.get(f"{API}/users/me", headers=auth_headers)).json()
    conversation_id = await _new_conversation(client, auth_headers)
    today = utcnow().date()
    async with session_factory() as session:
        session.add(
            UsageDaily(
                user_id=me["id"],
                usage_date=today,
                prompt_tokens=10,
                completion_tokens=0,
                request_count=1,
            )
        )
        await session.commit()

    monkeypatch.setattr(settings, "USER_DAILY_TOKEN_QUOTA", 10)
    monkeypatch.setattr(settings, "QUOTA_ENFORCE", True)

    response = await _chat(client, auth_headers, conversation_id)

    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "QUOTA_EXCEEDED"
    assert await fake_redis.get(f"quota:{me['id']}:{today:%Y%m%d}") == "10"


async def test_usage_cleanup(session_factory, fake_redis, monkeypatch):
    monkeypatch.setattr(tasks, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(settings, "USAGE_RETENTION_DAYS", 365)

    today = utcnow().date()
    async with session_factory() as session:
        session.add_all(
            [
                UsageDaily(
                    user_id=1,
                    usage_date=today - timedelta(days=400),
                    prompt_tokens=1,
                    completion_tokens=1,
                    request_count=1,
                ),
                UsageDaily(
                    user_id=1,
                    usage_date=today,
                    prompt_tokens=1,
                    completion_tokens=1,
                    request_count=1,
                ),
            ]
        )
        await session.commit()

    result = await tasks.cleanup_usage(None)

    assert result == {"deleted": 1}
    async with session_factory() as session:
        dates = list((await session.execute(select(UsageDaily.usage_date))).scalars())
    assert dates == [today]


async def test_quota_fail_open_when_redis_unavailable(monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr("app.services.usage_service.get_redis", unavailable)
    monkeypatch.setattr(settings, "USER_DAILY_TOKEN_QUOTA", 1)
    monkeypatch.setattr(settings, "QUOTA_ENFORCE", True)

    await UsageService().ensure_within_quota(1)
