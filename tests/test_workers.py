"""v2 WP2 后台摘要测试。"""

import app.services.chat_service as chat_module
import app.workers.tasks as tasks
from app.core.config import settings
from app.models.conversation import Conversation
from app.services.llm_service import LLMService, StreamChunk

API = "/api/v1"


def _aggressive(monkeypatch):
    monkeypatch.setattr(settings, "CHAT_SUMMARY_ENABLED", True)
    monkeypatch.setattr(settings, "CHAT_CONTEXT_MAX_TOKENS", 20)
    monkeypatch.setattr(settings, "CHAT_SUMMARY_TRIGGER_RATIO", 0.1)
    monkeypatch.setattr(settings, "CHAT_SUMMARY_KEEP_RECENT_MESSAGES", 1)


async def _chat(client, headers, cid, message):
    return await client.post(
        f"{API}/chat",
        headers=headers,
        json={"conversation_id": cid, "message": message},
    )


async def test_summary_task_enqueued(client, auth_headers, monkeypatch):
    _aggressive(monkeypatch)
    monkeypatch.setattr(settings, "SUMMARY_QUEUE_ENABLED", True)

    enqueued: list[int] = []

    async def fake_enqueue(conversation_id: int) -> None:
        enqueued.append(conversation_id)

    monkeypatch.setattr(chat_module, "enqueue_summary", fake_enqueue)

    async def fake_stream(self, messages):
        yield StreamChunk("delta", text="回复")
        yield StreamChunk("final", finish_reason="stop")

    monkeypatch.setattr(LLMService, "stream_chat", fake_stream)

    cid = (
        await client.post(f"{API}/conversations", headers=auth_headers, json={})
    ).json()["id"]
    for text in ("第一句", "第二句", "第三句"):
        await _chat(client, auth_headers, cid, text)

    assert enqueued


async def test_worker_summarize_task(
    client, auth_headers, session_factory, fake_redis, monkeypatch
):
    # 先用关闭摘要的方式造好历史消息
    monkeypatch.setattr(settings, "CHAT_SUMMARY_ENABLED", False)

    async def fake_stream(self, messages):
        yield StreamChunk("delta", text="回复")
        yield StreamChunk("final", finish_reason="stop")

    monkeypatch.setattr(LLMService, "stream_chat", fake_stream)
    cid = (
        await client.post(f"{API}/conversations", headers=auth_headers, json={})
    ).json()["id"]
    for text in ("第一句", "第二句", "第三句"):
        await _chat(client, auth_headers, cid, text)

    # worker：独立会话工厂 + 打桩摘要
    _aggressive(monkeypatch)
    monkeypatch.setattr(tasks, "AsyncSessionLocal", session_factory)

    async def fake_complete(self, messages, **kwargs):
        return "后台摘要"

    monkeypatch.setattr(LLMService, "complete", fake_complete)

    await tasks.summarize_conversation({}, cid)

    async with session_factory() as session:
        conversation = await session.get(Conversation, cid)
        assert conversation.summary == "后台摘要"
        assert conversation.summary_until_message_id is not None
