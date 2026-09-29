"""聊天 SSE 接口测试（LLM 打桩）。"""

import json

from sqlalchemy import select

from app.core.config import settings
from app.core.exceptions import LLMError
from app.models.message import Message
from app.services.llm_service import LLMService

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
            yield token

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


async def test_chat_llm_error(client, auth_headers, monkeypatch):
    async def failing_stream(self, messages):
        raise LLMError()
        yield ""  # noqa: unreachable  使其成为异步生成器

    monkeypatch.setattr(LLMService, "stream_chat", failing_stream)

    conversation_id = await _new_conversation(client, auth_headers)
    events = await _chat(client, auth_headers, conversation_id)

    error_event = next((data for name, data in events if name == "error"), None)
    assert error_event is not None
    assert error_event["code"] == "LLM_ERROR"
    assert "done" not in [name for name, _ in events]


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
        yield "ok"

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
