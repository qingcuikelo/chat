"""SQLAlchemy 2.0 异步引擎、会话与声明式基类。"""

from collections.abc import AsyncGenerator
from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

# 时间列：MySQL 使用 DATETIME(6) 微秒精度，其他方言使用通用 DateTime。
TimestampTZ = DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")


def utcnow() -> datetime:
    """UTC now（naive，含微秒），用于默认值与更新时间。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


def _engine_options() -> dict:
    options: dict = {"echo": settings.DB_ECHO, "future": True}
    if settings.DATABASE_URL.startswith("sqlite"):
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


engine = create_async_engine(settings.DATABASE_URL, **_engine_options())

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """返回会话工厂，供需要在请求范围外独立建会话的场景（如流式聊天）使用。"""
    return AsyncSessionLocal
