"""SQLAlchemy 2.0 异步引擎、会话与声明式基类。"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy import DateTime
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

logger = logging.getLogger(__name__)

# 时间列：MySQL 使用 DATETIME(6) 微秒精度，其他方言使用通用 DateTime。
TimestampTZ = DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")


def utcnow() -> datetime:
    """UTC now（naive，含微秒），用于默认值与更新时间。"""
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


def _engine_options(url: str) -> dict:
    options: dict = {"echo": settings.DB_ECHO, "future": True}
    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
    else:
        options.update(
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_MAX_OVERFLOW,
            pool_recycle=settings.DB_POOL_RECYCLE,
            pool_timeout=settings.DB_POOL_TIMEOUT,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 10},
        )
    return options


engine = create_async_engine(settings.DATABASE_URL, **_engine_options(settings.DATABASE_URL))


AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

# 只读从库引擎（v3 WP8a）：未配置/未启用时为 None，所有读取回退主库。
read_engine: AsyncEngine | None = None
AsyncReadSessionLocal: async_sessionmaker[AsyncSession] | None = None
if settings.DB_REPLICA_ENABLED and settings.DB_READ_URL:
    read_engine = create_async_engine(settings.DB_READ_URL, **_engine_options(settings.DB_READ_URL))
    AsyncReadSessionLocal = async_sessionmaker(
        bind=read_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
elif settings.DB_REPLICA_ENABLED:
    logger.warning("DB_REPLICA_ENABLED=true 但 DB_READ_URL 为空，读取回退主库")

_TRUTHY = {"1", "true", "yes", "on"}


async def _should_force_primary(request: Request) -> bool:
    """判断本次读取是否必须走主库（read-your-writes）。"""
    if request.query_params.get("consistent", "").lower() in _TRUTHY:
        return True
    if request.headers.get("X-Consistent-Read", "").lower() in _TRUTHY:
        return True

    raw = request.path_params.get("conversation_id")
    if raw is None:
        return False
    try:
        conversation_id = int(raw)
    except (TypeError, ValueError):
        return False

    # 延迟导入避免 database <-> cache_service 循环依赖
    from app.services.cache_service import CacheService

    return await CacheService().is_recently_written(conversation_id)


@asynccontextmanager
async def _session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with _session_scope(AsyncSessionLocal) as session:
        yield session


async def get_read_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """只读查询依赖：默认走从库，写后短窗口或显式 consistent 时回退主库。"""
    factory = AsyncReadSessionLocal if settings.DB_REPLICA_ENABLED else None
    if factory is not None and await _should_force_primary(request):
        factory = None
    async with _session_scope(factory or AsyncSessionLocal) as session:
        yield session


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """返回会话工厂，供需要在请求范围外独立建会话的场景（如流式聊天）使用。"""
    return AsyncSessionLocal


def get_read_session_factory() -> async_sessionmaker[AsyncSession] | None:
    """返回只读会话工厂；未启用读写分离时为 None。"""
    return AsyncReadSessionLocal


async def dispose_read_engine() -> None:
    if read_engine is not None:
        await read_engine.dispose()
