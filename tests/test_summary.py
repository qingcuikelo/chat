"""上下文滚动摘要测试。"""

from app.core.config import settings
from app.core.exceptions import LLMError
from app.models.conversation import Conversation
from app.services.llm_service import LLMService, StreamChunk

API = "/api/v1"


def _enable_aggressive_compression(monkeypatch):
    monkeypatch.setattr(settings, "CHAT_SUMMARY_ENABLED", True)
    monkeypatch.setattr(settings, "SUMMARY_QUEUE_ENABLED", False)
    monkeypatch.setattr(settings, "CHAT_CONTEXT_MAX_TOKENS", 20)
    monkeypatch.setattr(settings, "CHAT_SUMMARY_TRIGGER_RATIO", 0.1)
    monkeypatch.setattr(settings, "CHAT_SUMMARY_KEEP_RECENT_MESSAGES", 1)


async def _new_conversation(client, headers) -> int:
    response = await client.post(f"{API}/conversations", headers=headers, json={})
    return response.json()["id"]


async def _chat(client, headers, conversation_id: int, message: str):
    return await client.post(
        f"{API}/chat",
        headers=headers,
        json={"conversation_id": conversation_id, "message": message},
    )


async def test_summary_generated_and_injected(client, auth_headers, monkeypatch):
    _enable_aggressive_compression(monkeypatch)

    captured: dict = {"prompts": [], "complete_calls": 0}

    async def fake_stream(self, messages):
        captured["prompts"].append(messages)
        yield StreamChunk("delta", text="回复")
        yield StreamChunk("final", finish_reason="stop")

    async def fake_complete(self, messages, **kwargs):
        captured["complete_calls"] += 1
        return "这是滚动摘要"

    monkeypatch.setattr(LLMService, "stream_chat", fake_stream)
    monkeypatch.setattr(LLMService, "complete", fake_complete)

    conversation_id = await _new_conversation(client, auth_headers)
    for text in ("第一句", "第二句", "第三句"):
        response = await _chat(client, auth_headers, conversation_id, text)
        assert response.status_code == 200

    assert captured["complete_calls"] >= 1

    last_prompt = captured["prompts"][-1]
    assert any(
        message["role"] == "system" and "这是滚动摘要" in message["content"]
        for message in last_prompt
    )
    joined = " ".join(message["content"] for message in last_prompt)
    assert "第一句" not in joined


async def test_summary_stored_in_db(client, auth_headers, monkeypatch, db_session):
    _enable_aggressive_compression(monkeypatch)

    async def fake_stream(self, messages):
        yield StreamChunk("delta", text="回复")
        yield StreamChunk("final", finish_reason="stop")

    async def fake_complete(self, messages, **kwargs):
        return "落库摘要"

    monkeypatch.setattr(LLMService, "stream_chat", fake_stream)
    monkeypatch.setattr(LLMService, "complete", fake_complete)

    conversation_id = await _new_conversation(client, auth_headers)
    for text in ("第一句", "第二句", "第三句"):
        await _chat(client, auth_headers, conversation_id, text)

    conversation = await db_session.get(Conversation, conversation_id)
    await db_session.refresh(conversation)
    assert conversation.summary == "落库摘要"
    assert conversation.summary_until_message_id is not None


async def test_summary_disabled(client, auth_headers, monkeypatch):
    _enable_aggressive_compression(monkeypatch)
    monkeypatch.setattr(settings, "CHAT_SUMMARY_ENABLED", False)

    calls = {"complete": 0}

    async def fake_stream(self, messages):
        yield StreamChunk("delta", text="回复")
        yield StreamChunk("final", finish_reason="stop")

    async def fake_complete(self, messages, **kwargs):
        calls["complete"] += 1
        return "不应调用"

    monkeypatch.setattr(LLMService, "stream_chat", fake_stream)
    monkeypatch.setattr(LLMService, "complete", fake_complete)

    conversation_id = await _new_conversation(client, auth_headers)
    for text in ("第一句", "第二句", "第三句"):
        await _chat(client, auth_headers, conversation_id, text)

    assert calls["complete"] == 0


async def test_summary_failure_falls_back(client, auth_headers, monkeypatch, db_session):
    _enable_aggressive_compression(monkeypatch)

    async def fake_stream(self, messages):
        yield StreamChunk("delta", text="回复")
        yield StreamChunk("final", finish_reason="stop")

    async def failing_complete(self, messages, **kwargs):
        raise LLMError()

    monkeypatch.setattr(LLMService, "stream_chat", fake_stream)
    monkeypatch.setattr(LLMService, "complete", failing_complete)

    conversation_id = await _new_conversation(client, auth_headers)
    for text in ("第一句", "第二句", "第三句"):
        response = await _chat(client, auth_headers, conversation_id, text)
        assert response.status_code == 200
        assert "done" in response.text

    conversation = await db_session.get(Conversation, conversation_id)
    await db_session.refresh(conversation)
    assert conversation.summary is None
