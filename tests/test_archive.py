"""v3 WP8b 消息归档测试。"""

from datetime import timedelta

from sqlalchemy import func, select

import app.workers.tasks as tasks
from app.core.config import settings
from app.core.database import utcnow
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.message_archive import MessageArchive
from app.repositories.message_repository import MessageRepository
from app.services.chat_service import ChatService


async def _seed(session_factory, *, old: int, recent: int) -> int:
    async with session_factory() as session:
        conversation = Conversation(user_id=1, title="t")
        session.add(conversation)
        await session.flush()
        conversation_id = conversation.id

        for index in range(old):
            session.add(
                Message(
                    id=100 + index,
                    conversation_id=conversation_id,
                    role="user",
                    content=f"old-{index}",
                    token_count=1,
                    created_at=utcnow() - timedelta(days=200),
                )
            )
        for index in range(recent):
            session.add(
                Message(
                    id=200 + index,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=f"new-{index}",
                    token_count=1,
                    created_at=utcnow(),
                )
            )
        await session.commit()
        return conversation_id


async def _archive(session_factory, monkeypatch, **kwargs) -> dict[str, int]:
    monkeypatch.setattr(tasks, "AsyncSessionLocal", session_factory)
    return await tasks.archive_messages(None, **kwargs)


async def test_archive_moves_old_messages_in_batches(session_factory, fake_redis, monkeypatch):
    await _seed(session_factory, old=5, recent=2)
    monkeypatch.setattr(settings, "MESSAGE_ARCHIVE_ENABLED", True)

    result = await _archive(session_factory, monkeypatch, after_days=180, batch_size=2)
    assert result == {"archived": 5}

    async with session_factory() as session:
        hot = list((await session.execute(select(Message.id).order_by(Message.id))).scalars())
        archived = list(
            (await session.execute(select(MessageArchive.id).order_by(MessageArchive.id))).scalars()
        )

    assert hot == [200, 201]
    assert archived == [100, 101, 102, 103, 104]


async def test_archive_is_idempotent(session_factory, fake_redis, monkeypatch):
    await _seed(session_factory, old=3, recent=1)
    monkeypatch.setattr(settings, "MESSAGE_ARCHIVE_ENABLED", True)

    first = await _archive(session_factory, monkeypatch, after_days=180, batch_size=2)
    second = await _archive(session_factory, monkeypatch, after_days=180, batch_size=2)

    assert first == {"archived": 3}
    assert second == {"archived": 0}


async def test_archive_disabled_keeps_messages(session_factory, fake_redis, monkeypatch):
    await _seed(session_factory, old=2, recent=1)
    monkeypatch.setattr(settings, "MESSAGE_ARCHIVE_ENABLED", False)

    result = await _archive(session_factory, monkeypatch, after_days=0)

    assert result == {"archived": 0}
    async with session_factory() as session:
        hot = await session.scalar(select(func.count()).select_from(Message))
    assert hot == 3


async def test_archive_respects_lock(session_factory, fake_redis, monkeypatch):
    await _seed(session_factory, old=2, recent=0)
    monkeypatch.setattr(settings, "MESSAGE_ARCHIVE_ENABLED", True)
    await fake_redis.set(tasks.ARCHIVE_LOCK_KEY, "1", ex=60)

    result = await _archive(session_factory, monkeypatch, after_days=180)

    assert result == {"archived": 0}
    async with session_factory() as session:
        hot = await session.scalar(select(func.count()).select_from(Message))
    assert hot == 2


async def test_repository_reads_union_of_hot_and_archive(session_factory, fake_redis, monkeypatch):
    conversation_id = await _seed(session_factory, old=4, recent=2)
    monkeypatch.setattr(settings, "MESSAGE_ARCHIVE_ENABLED", True)
    await _archive(session_factory, monkeypatch, after_days=180, batch_size=10)

    async with session_factory() as session:
        repository = MessageRepository(session)
        items, total = await repository.list_by_conversation(conversation_id, page=1, page_size=3)
        assert total == 6
        assert [item.id for item in items] == [100, 101, 102]

        page2, _ = await repository.list_by_conversation(conversation_id, page=2, page_size=3)
        assert [item.id for item in page2] == [103, 200, 201]

        keyset = await repository.list_keyset_after(conversation_id, 101, 2)
        assert [item.id for item in keyset] == [102, 103]

        backwards = await repository.list_keyset_before(conversation_id, 200, 2)
        assert [item.id for item in backwards] == [103, 102]

        assert await repository.max_id() == 201


async def test_delete_by_conversation_clears_archive(session_factory, fake_redis, monkeypatch):
    conversation_id = await _seed(session_factory, old=3, recent=1)
    monkeypatch.setattr(settings, "MESSAGE_ARCHIVE_ENABLED", True)
    await _archive(session_factory, monkeypatch, after_days=180, batch_size=10)

    async with session_factory() as session:
        repository = MessageRepository(session)
        await repository.delete_by_conversation(conversation_id)
        await session.commit()

        hot = await session.scalar(select(func.count()).select_from(Message))
        archived = await session.scalar(select(func.count()).select_from(MessageArchive))

    assert hot == 0
    assert archived == 0


async def test_delete_by_user_clears_archive(session_factory, fake_redis, monkeypatch):
    await _seed(session_factory, old=2, recent=1)
    monkeypatch.setattr(settings, "MESSAGE_ARCHIVE_ENABLED", True)
    await _archive(session_factory, monkeypatch, after_days=180, batch_size=10)

    async with session_factory() as session:
        repository = MessageRepository(session)
        await repository.delete_by_user(1)
        await session.commit()

        hot = await session.scalar(select(func.count()).select_from(Message))
        archived = await session.scalar(select(func.count()).select_from(MessageArchive))

    assert hot == 0
    assert archived == 0


async def test_max_id_considers_archive(session_factory, fake_redis, monkeypatch):
    conversation_id = await _seed(session_factory, old=1, recent=1)
    async with session_factory() as session:
        session.add(
            MessageArchive(
                id=900,
                conversation_id=conversation_id,
                role="user",
                content="archived-high-id",
                token_count=1,
            )
        )
        await session.commit()

    async with session_factory() as session:
        repository = MessageRepository(session)
        assert await repository.max_id() == 900


async def test_message_id_allocator_seeds_from_archive(session_factory, fake_redis, monkeypatch):
    conversation_id = await _seed(session_factory, old=2, recent=1)
    async with session_factory() as session:
        session.add(
            MessageArchive(
                id=800,
                conversation_id=conversation_id,
                role="user",
                content="archived",
                token_count=1,
            )
        )
        await session.commit()

    await fake_redis.delete("chat:message_id")
    service = ChatService(session_factory=session_factory)
    assert await service._allocate_message_id() == 801
