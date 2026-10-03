"""v2 WP1 写路径瘦身测试。"""

from sqlalchemy import event

from app.core.security import hash_password
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository
from app.repositories.user_repository import UserRepository
from app.services.llm_service import LLMService, StreamChunk

API = "/api/v1"


async def test_message_create_does_not_refresh(session_factory, monkeypatch):
    async with session_factory() as session:
        user = await UserRepository(session).create(
            username="u1", email="u1@example.com", password_hash=hash_password("Passw0rd!")
        )
        conversation = await ConversationRepository(session).create(
            user_id=user.id, title="t"
        )
        await session.commit()

        async def boom(*args, **kwargs):
            raise AssertionError("refresh() must not be called on insert")

        monkeypatch.setattr(session, "refresh", boom, raising=False)

        message = await MessageRepository(session).create(
            conversation_id=conversation.id, role="user", content="hi", token_count=1
        )
        await session.commit()
        assert message.id is not None
        assert message.created_at is not None


async def test_single_chat_turn_commits_once(client, auth_headers, db_engine, monkeypatch):
    async def fake_stream(self, messages):
        yield StreamChunk("delta", text="回复")
        yield StreamChunk("final", finish_reason="stop")

    monkeypatch.setattr(LLMService, "stream_chat", fake_stream)

    created = await client.post(f"{API}/conversations", headers=auth_headers, json={})
    conversation_id = created.json()["id"]

    commits: list[int] = []
    on_commit = lambda conn: commits.append(1)  # noqa: E731
    event.listen(db_engine.sync_engine, "commit", on_commit)

    try:
        response = await client.post(
            f"{API}/chat",
            headers=auth_headers,
            json={"conversation_id": conversation_id, "message": "hi"},
        )
        assert response.status_code == 200
    finally:
        event.remove(db_engine.sync_engine, "commit", on_commit)

    assert len(commits) == 1
