"""v3 WP8c 写队列测试。"""

from sqlalchemy import event, select

import app.services.write_queue as write_queue
from app.core.config import settings
from app.core.database import utcnow
from app.core.exceptions import LLMError
from app.models.conversation import Conversation
from app.models.message import Message
from app.services.llm_service import LLMService, StreamChunk
from app.services.write_queue import WriteTurn

API = "/api/v1"


async def _new_conversation(client, headers) -> int:
    response = await client.post(f"{API}/conversations", headers=headers, json={})
    return response.json()["id"]


async def _chat(client, headers, conversation_id: int, message: str = "hi") -> None:
    response = await client.post(
        f"{API}/chat",
        headers=headers,
        json={"conversation_id": conversation_id, "message": message},
    )
    assert response.status_code == 200, response.text


async def _messages(session_factory, conversation_id: int) -> list[Message]:
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.id)
                )
            ).scalars()
        )


async def _fake_stream(self, messages):
    yield StreamChunk("delta", text="回复")
    yield StreamChunk("final", finish_reason="stop")


async def test_queue_enabled_defers_then_consumer_persists(
    client,
    auth_headers,
    monkeypatch,
    session_factory,
    fake_redis,
):
    monkeypatch.setattr(settings, "WRITE_QUEUE_ENABLED", True)
    monkeypatch.setattr(LLMService, "stream_chat", _fake_stream)
    await fake_redis.set(write_queue.HEARTBEAT_KEY, "1")

    conversation_id = await _new_conversation(client, auth_headers)
    await _chat(client, auth_headers, conversation_id, message="你好")

    assert await _messages(session_factory, conversation_id) == []

    written = await write_queue.consume_once("test", session_factory=session_factory)
    assert written == 1

    rows = await _messages(session_factory, conversation_id)
    assert [row.role for row in rows] == ["user", "assistant"]
    assert rows[0].content == "你好"
    assert rows[1].content == "回复"

    async with session_factory() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        assert conversation.title == "你好"


async def test_queue_without_heartbeat_falls_back_to_direct_write(
    client,
    auth_headers,
    monkeypatch,
    session_factory,
    fake_redis,
):
    monkeypatch.setattr(settings, "WRITE_QUEUE_ENABLED", True)
    monkeypatch.setattr(LLMService, "stream_chat", _fake_stream)

    conversation_id = await _new_conversation(client, auth_headers)
    await _chat(client, auth_headers, conversation_id)

    rows = await _messages(session_factory, conversation_id)
    assert [row.role for row in rows] == ["user", "assistant"]
    assert await fake_redis.xlen(write_queue.STREAM_KEY) == 0


async def test_queue_failure_turn_persists_placeholder(
    client,
    auth_headers,
    monkeypatch,
    session_factory,
    fake_redis,
):
    async def failing_stream(self, messages):
        raise LLMError()
        yield StreamChunk("delta", text="")  # unreachable

    monkeypatch.setattr(settings, "WRITE_QUEUE_ENABLED", True)
    monkeypatch.setattr(LLMService, "stream_chat", failing_stream)
    await fake_redis.set(write_queue.HEARTBEAT_KEY, "1")

    conversation_id = await _new_conversation(client, auth_headers)
    await _chat(client, auth_headers, conversation_id, message="失败消息")

    assert await _messages(session_factory, conversation_id) == []

    await write_queue.consume_once("test", session_factory=session_factory)

    rows = await _messages(session_factory, conversation_id)
    assert [row.role for row in rows] == ["user", "assistant"]
    assert rows[0].token_count == 0
    assert rows[0].metadata_["raw_token_count"] > 0
    assert rows[1].token_count == 0
    assert rows[1].metadata_["status"] == "failed"
    assert rows[1].metadata_["error_code"] == "LLM_ERROR"
    assert rows[1].metadata_["answer_to"] == rows[0].id


async def test_process_turns_batch_commits_once(session_factory, fake_redis, db_engine):
    async with session_factory() as session:
        conversation = Conversation(user_id=1, title=None)
        session.add(conversation)
        await session.commit()
        conversation_id = conversation.id

    now = utcnow()
    turns = [
        WriteTurn(
            request_id="req-batch-1",
            conversation_id=conversation_id,
            user_id=1,
            user_message_id=501,
            assistant_message_id=502,
            user_content="u1",
            assistant_content="a1",
            user_token_count=1,
            assistant_token_count=1,
            created_at=now,
        ),
        WriteTurn(
            request_id="req-batch-2",
            conversation_id=conversation_id,
            user_id=1,
            user_message_id=503,
            assistant_message_id=504,
            user_content="u2",
            assistant_content="a2",
            user_token_count=1,
            assistant_token_count=1,
            created_at=now,
        ),
    ]

    commits: list[int] = []
    on_commit = lambda conn: commits.append(1)  # noqa: E731
    event.listen(db_engine.sync_engine, "commit", on_commit)
    try:
        written = await write_queue.process_turns(turns, session_factory)
    finally:
        event.remove(db_engine.sync_engine, "commit", on_commit)

    assert written == 2
    assert len(commits) == 1
    assert len(await _messages(session_factory, conversation_id)) == 4


async def test_process_turns_is_idempotent(session_factory, fake_redis):
    async with session_factory() as session:
        conversation = Conversation(user_id=1, title=None)
        session.add(conversation)
        await session.commit()
        conversation_id = conversation.id

    turn = WriteTurn(
        request_id="req-idem",
        conversation_id=conversation_id,
        user_id=1,
        user_message_id=601,
        assistant_message_id=602,
        user_content="u",
        assistant_content="a",
        user_token_count=1,
        assistant_token_count=1,
        created_at=utcnow(),
    )

    await write_queue.process_turns([turn], session_factory)
    await write_queue.process_turns([turn], session_factory)

    assert len(await _messages(session_factory, conversation_id)) == 2


async def test_invalid_payload_goes_to_dead_letter(session_factory, fake_redis):
    await fake_redis.xadd(write_queue.STREAM_KEY, {"payload": "not-json"})

    written = await write_queue.consume_once("test", session_factory=session_factory)

    assert written == 0
    dead = await fake_redis.xrange(write_queue.DEAD_KEY)
    assert len(dead) == 1
    assert dead[0][1]["payload"] == "not-json"
    assert dead[0][1]["reason"] == "invalid_payload"
    assert await fake_redis.xlen(write_queue.STREAM_KEY) == 0
