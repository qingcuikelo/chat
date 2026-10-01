"""聊天 SSE 接口测试（LLM 打桩）。"""

import asyncio
import json

from sqlalchemy import select

from app.core.config import settings
from app.core.exceptions import LLMError
from app.models.message import Message
from app.services.chat_service import ChatService
from app.services.llm_service import LLMService, StreamChunk

API = "/api/v1"


def parse_sse(text: str) -> list[tuple[str, dict | None]]:
    events: list[tuple[str, dict | None]] = []
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    for block in normalized.strip().split("\n\n"):
        event_name: str | None = None
        data: str | None = None
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data = line[len("data:") :].strip()
        if event_name:
            events.append((event_name, json.loads(data) if data else None))
    return events


async def _new_conversation(client, headers) -> int:
    response = await client.post(
        f"{API}/conversations", headers=headers, json={"title": "聊天"}
    )
    return response.json()["id"]


async def _chat(client, headers, conversation_id: int, message: str = "hi"):
    response = await client.post(
        f"{API}/chat",
        headers=headers,
        json={"conversation_id": conversation_id, "message": message},
    )
    assert response.status_code == 200, response.text
    return parse_sse(response.text)


async def test_chat_stream_success(client, auth_headers, monkeypatch, db_session):
    async def fake_stream(self, messages):
        for token in ["你好", "，", "世界", "😊"]:
            yield StreamChunk("delta", text=token)
        yield StreamChunk("final", finish_reason="stop")

    monkeypatch.setattr(LLMService, "stream_chat", fake_stream)

    conversation_id = await _new_conversation(client, auth_headers)
    events = await _chat(client, auth_headers, conversation_id)

    names = [name for name, _ in events]
    assert names[0] == "start"
    assert names[-1] == "done"
    assert "delta" in names
    assert "error" not in names

    rows = (
        await db_session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id)
        )
    ).scalars().all()
    assert [row.role for row in rows] == ["user", "assistant"]
    assert rows[1].content == "你好，世界😊"

    done_payload = next(data for name, data in events if name == "done")
    assert done_payload["assistant_message_id"] == rows[1].id


async def test_chat_llm_error(client, auth_headers, monkeypatch, db_session):
    async def failing_stream(self, messages):
        raise LLMError()
        yield StreamChunk("delta", text="")  # noqa: unreachable  使其成为异步生成器

    monkeypatch.setattr(LLMService, "stream_chat", failing_stream)

    conversation_id = await _new_conversation(client, auth_headers)
    events = await _chat(client, auth_headers, conversation_id)

    error_event = next((data for name, data in events if name == "error"), None)
    assert error_event is not None
    assert error_event["code"] == "LLM_ERROR"
    assert "done" not in [name for name, _ in events]

    rows = (
        await db_session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id)
        )
    ).scalars().all()
    assert [row.role for row in rows] == ["user", "assistant"]
    assert rows[0].token_count == 0
    assert rows[1].token_count == 0
    assert rows[1].metadata_["status"] == "failed"
    assert rows[1].metadata_["error_code"] == "LLM_ERROR"
    assert rows[1].metadata_["answer_to"] == rows[0].id


async def test_chat_requires_token(client):
    response = await client.post(
        f"{API}/chat", json={"conversation_id": 1, "message": "hi"}
    )
    assert response.status_code == 401


async def test_chat_conversation_not_found(client, auth_headers):
    response = await client.post(
        f"{API}/chat",
        headers=auth_headers,
        json={"conversation_id": 999999, "message": "hi"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "CONVERSATION_NOT_FOUND"


async def test_chat_rate_limited(client, auth_headers, monkeypatch):
    async def fake_stream(self, messages):
        yield StreamChunk("delta", text="ok")
        yield StreamChunk("final", finish_reason="stop")

    monkeypatch.setattr(LLMService, "stream_chat", fake_stream)
    monkeypatch.setattr(settings, "RATE_LIMIT_PER_MINUTE", 1)

    conversation_id = await _new_conversation(client, auth_headers)
    await _chat(client, auth_headers, conversation_id)

    response = await client.post(
        f"{API}/chat",
        headers=auth_headers,
        json={"conversation_id": conversation_id, "message": "again"},
    )
    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "RATE_LIMITED"


def test_build_prompt_respects_token_budget(monkeypatch):
    monkeypatch.setattr(settings, "CHAT_CONTEXT_MAX_TOKENS", 5)
    history = [
        {"role": "user", "content": "a" * 100},
        {"role": "assistant", "content": "b" * 100},
        {"role": "user", "content": "cccc"},
    ]

    prompt = ChatService._build_prompt(history)

    assert prompt[0]["role"] == "system"
    assert len(prompt) == 2
    assert prompt[1]["content"] == "cccc"


async def test_conversation_title_auto_generated(client, auth_headers, monkeypatch):
    async def fake_stream(self, messages):
        yield StreamChunk("delta", text="ok")
        yield StreamChunk("final", finish_reason="stop")

    monkeypatch.setattr(LLMService, "stream_chat", fake_stream)

    created = await client.post(f"{API}/conversations", headers=auth_headers, json={})
    assert created.status_code == 201
    conversation_id = created.json()["id"]
    assert created.json()["title"] is None

    await client.post(
        f"{API}/chat",
        headers=auth_headers,
        json={"conversation_id": conversation_id, "message": "  如何   学习 Python  "},
    )

    detail = (
        await client.get(f"{API}/conversations/{conversation_id}", headers=auth_headers)
    ).json()
    assert detail["title"] == "如何 学习 Python"

    await client.post(
        f"{API}/chat",
        headers=auth_headers,
        json={"conversation_id": conversation_id, "message": "第二条消息"},
    )
    detail_again = (
        await client.get(f"{API}/conversations/{conversation_id}", headers=auth_headers)
    ).json()
    assert detail_again["title"] == "如何 学习 Python"


async def test_explicit_title_preserved(client, auth_headers, monkeypatch):
    async def fake_stream(self, messages):
        yield StreamChunk("delta", text="ok")
        yield StreamChunk("final", finish_reason="stop")

    monkeypatch.setattr(LLMService, "stream_chat", fake_stream)

    created = await client.post(
        f"{API}/conversations", headers=auth_headers, json={"title": "我的会话"}
    )
    conversation_id = created.json()["id"]

    await client.post(
        f"{API}/chat",
        headers=auth_headers,
        json={"conversation_id": conversation_id, "message": "随便说点"},
    )

    detail = (
        await client.get(f"{API}/conversations/{conversation_id}", headers=auth_headers)
    ).json()
    assert detail["title"] == "我的会话"


async def test_delta_batching(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "SSE_BATCH_MIN_CHARS", 4)
    monkeypatch.setattr(settings, "SSE_BATCH_MAX_INTERVAL_MS", 100000)

    async def fake_stream(self, messages):
        for token in ["ab", "cd", "ef"]:
            yield StreamChunk("delta", text=token)
        yield StreamChunk("final", finish_reason="stop")

    monkeypatch.setattr(LLMService, "stream_chat", fake_stream)

    conversation_id = await _new_conversation(client, auth_headers)
    events = await _chat(client, auth_headers, conversation_id)

    deltas = [data["content"] for name, data in events if name == "delta"]
    assert deltas == ["abcd", "ef"]


async def test_finish_reason_and_usage_passthrough(
    client, auth_headers, monkeypatch, db_session
):
    async def fake_stream(self, messages):
        yield StreamChunk("delta", text="部分")
        yield StreamChunk(
            "final", finish_reason="length", usage={"completion_tokens": 7}
        )

    monkeypatch.setattr(LLMService, "stream_chat", fake_stream)

    conversation_id = await _new_conversation(client, auth_headers)
    events = await _chat(client, auth_headers, conversation_id)

    done = next(data for name, data in events if name == "done")
    assert done["finish_reason"] == "length"
    assert done["token_count"] == 7

    rows = (
        await db_session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id)
        )
    ).scalars().all()
    assert rows[1].token_count == 7


async def test_failed_turn_excluded_from_prompt(client, auth_headers, monkeypatch):
    async def failing_stream(self, messages):
        raise LLMError()
        yield StreamChunk("delta", text="")  # noqa: unreachable

    monkeypatch.setattr(LLMService, "stream_chat", failing_stream)
    conversation_id = await _new_conversation(client, auth_headers)
    await _chat(client, auth_headers, conversation_id, message="失败消息")

    captured: dict = {}

    async def capturing_stream(self, messages):
        captured["prompt"] = messages
        yield StreamChunk("delta", text="ok")
        yield StreamChunk("final", finish_reason="stop")

    monkeypatch.setattr(LLMService, "stream_chat", capturing_stream)
    await _chat(client, auth_headers, conversation_id, message="新消息")

    joined = " ".join(message["content"] for message in captured["prompt"])
    assert "失败消息" not in joined
    user_messages = [
        message["content"]
        for message in captured["prompt"]
        if message["role"] == "user"
    ]
    assert user_messages == ["新消息"]


async def test_interrupted_turn_persisted(
    client, auth_headers, session_factory, monkeypatch
):
    conversation_id = await _new_conversation(client, auth_headers)

    async def cancelling_stream(self, messages):
        yield StreamChunk("delta", text="部分")
        raise asyncio.CancelledError()

    monkeypatch.setattr(LLMService, "stream_chat", cancelling_stream)

    service = ChatService(session_factory=session_factory)
    try:
        async for _ in service.stream_chat(
            conversation_id=conversation_id, user_message="hi"
        ):
            pass
    except asyncio.CancelledError:
        pass

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.id)
            )
        ).scalars().all()

    assert [row.role for row in rows] == ["user", "assistant"]
    assert rows[0].token_count == 0
    assert rows[1].content == "部分"
    assert rows[1].token_count == 0
    assert rows[1].metadata_["status"] == "interrupted"
    assert rows[1].metadata_["answer_to"] == rows[0].id
