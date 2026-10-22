"""v3 WP8a 读写分离测试。"""

from types import SimpleNamespace

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.core.database as database
from app.core.database import Base, get_read_db
from app.main import app
from app.models.conversation import Conversation
from app.services.cache_service import CacheService

API = "/api/v1"


def _request(**kwargs) -> SimpleNamespace:
    return SimpleNamespace(
        query_params=kwargs.get("query_params", {}),
        headers=kwargs.get("headers", {}),
        path_params=kwargs.get("path_params", {}),
    )


async def _titles(request) -> list[str]:
    agen = database.get_read_db(request)  # type: ignore[arg-type]
    try:
        session = await agen.__anext__()
        result = await session.execute(select(Conversation.title).order_by(Conversation.id))
        return list(result.scalars())
    finally:
        await agen.aclose()


async def _make_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture
async def two_dbs():
    primary_engine, primary_factory = await _make_factory()
    replica_engine, replica_factory = await _make_factory()

    async with primary_factory() as session:
        session.add(Conversation(user_id=1, title="primary"))
        await session.commit()
    async with replica_factory() as session:
        session.add(Conversation(user_id=1, title="replica"))
        await session.commit()

    yield primary_factory, replica_factory

    await primary_engine.dispose()
    await replica_engine.dispose()


async def test_flag_off_uses_primary(two_dbs, monkeypatch):
    primary_factory, replica_factory = two_dbs
    monkeypatch.setattr(database, "AsyncSessionLocal", primary_factory)
    monkeypatch.setattr(database, "AsyncReadSessionLocal", replica_factory)
    monkeypatch.setattr(database.settings, "DB_REPLICA_ENABLED", False)

    assert await _titles(_request()) == ["primary"]


async def test_flag_on_uses_replica(two_dbs, monkeypatch):
    primary_factory, replica_factory = two_dbs
    monkeypatch.setattr(database, "AsyncSessionLocal", primary_factory)
    monkeypatch.setattr(database, "AsyncReadSessionLocal", replica_factory)
    monkeypatch.setattr(database.settings, "DB_REPLICA_ENABLED", True)

    assert await _titles(_request()) == ["replica"]


async def test_consistent_query_forces_primary(two_dbs, monkeypatch):
    primary_factory, replica_factory = two_dbs
    monkeypatch.setattr(database, "AsyncSessionLocal", primary_factory)
    monkeypatch.setattr(database, "AsyncReadSessionLocal", replica_factory)
    monkeypatch.setattr(database.settings, "DB_REPLICA_ENABLED", True)

    request = _request(query_params={"consistent": "1"})
    assert await _titles(request) == ["primary"]


async def test_consistent_header_forces_primary(two_dbs, monkeypatch):
    primary_factory, replica_factory = two_dbs
    monkeypatch.setattr(database, "AsyncSessionLocal", primary_factory)
    monkeypatch.setattr(database, "AsyncReadSessionLocal", replica_factory)
    monkeypatch.setattr(database.settings, "DB_REPLICA_ENABLED", True)

    request = _request(headers={"X-Consistent-Read": "1"})
    assert await _titles(request) == ["primary"]


async def test_recent_write_sticky_forces_primary(two_dbs, monkeypatch, fake_redis):
    primary_factory, replica_factory = two_dbs
    monkeypatch.setattr(database, "AsyncSessionLocal", primary_factory)
    monkeypatch.setattr(database, "AsyncReadSessionLocal", replica_factory)
    monkeypatch.setattr(database.settings, "DB_REPLICA_ENABLED", True)

    async with primary_factory() as session:
        conversation = Conversation(user_id=1, title="sticky")
        session.add(conversation)
        await session.commit()
        conversation_id = conversation.id

    await CacheService().mark_conversation_write(conversation_id)
    request = _request(path_params={"conversation_id": str(conversation_id)})

    assert "sticky" in await _titles(request)
    assert "replica" not in await _titles(request)


async def test_conversation_list_uses_read_dependency(client, auth_headers, monkeypatch):
    me = (await client.get(f"{API}/users/me", headers=auth_headers)).json()
    replica_engine, replica_factory = await _make_factory()
    async with replica_factory() as session:
        session.add(Conversation(user_id=me["id"], title="replica-only"))
        await session.commit()

    async def override_read():
        async with replica_factory() as session:
            yield session

    app.dependency_overrides[get_read_db] = override_read
    try:
        response = await client.get(f"{API}/conversations", headers=auth_headers)
    finally:
        app.dependency_overrides.pop(get_read_db, None)
        await replica_engine.dispose()

    assert response.status_code == 200
    titles = [item["title"] for item in response.json()["items"]]
    assert "replica-only" in titles
