"""v2 WP6 批量删除测试。"""

from sqlalchemy import func, select

from app.models.conversation import Conversation
from app.models.message import Message
from app.services.llm_service import LLMService, StreamChunk

API = "/api/v1"


async def _chat(client, headers, conversation_id, monkeypatch):
    async def fake_stream(self, messages):
        yield StreamChunk("delta", text="回复")
        yield StreamChunk("final", finish_reason="stop")

    monkeypatch.setattr(LLMService, "stream_chat", fake_stream)
    await client.post(
        f"{API}/chat",
        headers=headers,
        json={"conversation_id": conversation_id, "message": "hi"},
    )


async def _count_messages(session_factory, conversation_id: int) -> int:
    async with session_factory() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == conversation_id)
            )
        )


async def test_delete_conversation_removes_messages(
    client, auth_headers, session_factory, monkeypatch
):
    conversation_id = (
        await client.post(f"{API}/conversations", headers=auth_headers, json={})
    ).json()["id"]
    await _chat(client, auth_headers, conversation_id, monkeypatch)
    assert await _count_messages(session_factory, conversation_id) == 2

    response = await client.delete(
        f"{API}/conversations/{conversation_id}", headers=auth_headers
    )
    assert response.status_code == 204
    assert await _count_messages(session_factory, conversation_id) == 0


async def test_delete_user_removes_conversations_and_messages(
    client, auth_headers, session_factory, monkeypatch
):
    conversation_id = (
        await client.post(f"{API}/conversations", headers=auth_headers, json={})
    ).json()["id"]
    await _chat(client, auth_headers, conversation_id, monkeypatch)

    response = await client.delete(f"{API}/users/me", headers=auth_headers)
    assert response.status_code == 204

    async with session_factory() as session:
        conversations = await session.scalar(
            select(func.count()).select_from(Conversation)
        )
        messages = await session.scalar(select(func.count()).select_from(Message))
    assert conversations == 0
    assert messages == 0
