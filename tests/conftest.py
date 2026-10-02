"""测试夹具：隔离的内存 SQLite + 伪造 Redis + 桩 LLM。"""

import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.core.redis as redis_module
import app.models  # noqa: F401  确保模型完成注册
from app.core.database import Base, get_db, get_session_factory
from app.core.security import create_email_verification_token
from app.main import app

API_PREFIX = "/api/v1"


@pytest_asyncio.fixture(autouse=True)
async def _stub_enqueue_verification_email(monkeypatch):
    """默认不打桩真实 ARQ：验证邮件投递改为 no-op。"""

    async def _noop(*args, **kwargs):
        return None

    import app.workers.queue as queue_module

    monkeypatch.setattr(queue_module, "enqueue_verification_email", _noop)


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(db_engine):
    return async_sessionmaker(bind=db_engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture
async def db_session(session_factory):
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def fake_redis(monkeypatch):
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis_module, "_client", fake)
    yield fake
    await fake.aclose()


@pytest_asyncio.fixture
async def client(session_factory, fake_redis):
    async def override_get_db():
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_factory] = lambda: session_factory

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def create_user_token(client):
    """返回一个 helper：注册并登录指定用户，返回带 Bearer 的请求头。"""

    async def _create(
        username: str = "alice",
        email: str = "alice@example.com",
        password: str = "Passw0rd!",
    ) -> dict[str, str]:
        registered = await client.post(
            f"{API_PREFIX}/auth/register",
            json={"username": username, "email": email, "password": password},
        )
        user_id = registered.json()["id"]
        # 直接生成验证令牌完成邮箱验证（不依赖 SMTP / worker）
        token = create_email_verification_token(user_id, email)
        await client.post(f"{API_PREFIX}/auth/verify-email", json={"token": token})

        response = await client.post(
            f"{API_PREFIX}/auth/login",
            json={"username": username, "password": password},
        )
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _create


@pytest_asyncio.fixture
async def auth_headers(create_user_token):
    return await create_user_token()
