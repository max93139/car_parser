"""
Database connection, engine creation, and async session management.
Supports both PostgreSQL (asyncpg) and SQLite (aiosqlite).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool, StaticPool

from src.config import get_settings

logger = logging.getLogger(__name__)

_global_engine: Optional[AsyncEngine] = None
_global_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def get_async_engine(db_url: Optional[str] = None, **kwargs: Any) -> AsyncEngine:
    """
    Creates or returns the cached AsyncEngine.
    Optimizes connection pool settings based on database dialect (PostgreSQL vs SQLite).
    """
    global _global_engine
    if _global_engine is not None and db_url is None:
        return _global_engine

    target_url = db_url or get_settings().database.url

    engine_kwargs: Dict[str, Any] = {
        "echo": kwargs.get("echo", False),
    }

    if target_url.startswith("sqlite"):
        # SQLite configuration
        if ":memory:" in target_url:
            engine_kwargs["poolclass"] = StaticPool
            engine_kwargs["connect_args"] = {"check_same_thread": False}
        else:
            engine_kwargs["poolclass"] = NullPool

        engine = create_async_engine(target_url, **engine_kwargs)

        # Enforce SQLite foreign key constraints
        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection: Any, connection_record: Any) -> None:
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

    else:
        # PostgreSQL / asyncpg configuration
        settings = get_settings()
        engine_kwargs.update(
            {
                "poolclass": AsyncAdaptedQueuePool,
                "pool_size": kwargs.get("pool_size", settings.database.pool_size),
                "max_overflow": kwargs.get("max_overflow", settings.database.max_overflow),
                "pool_timeout": kwargs.get("pool_timeout", settings.database.pool_timeout),
                "pool_recycle": kwargs.get("pool_recycle", 300),
                "pool_pre_ping": kwargs.get("pool_pre_ping", True),
            }
        )
        engine = create_async_engine(target_url, **engine_kwargs)

    if db_url is None:
        _global_engine = engine

    return engine


def get_session_factory(engine: Optional[AsyncEngine] = None) -> async_sessionmaker[AsyncSession]:
    """
    Returns an async sessionmaker bound to the given or global engine.
    """
    global _global_session_factory
    if _global_session_factory is not None and engine is None:
        return _global_session_factory

    active_engine = engine or get_async_engine()
    factory = async_sessionmaker(
        bind=active_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    if engine is None:
        _global_session_factory = factory

    return factory


@asynccontextmanager
async def get_db_session(
    engine: Optional[AsyncEngine] = None,
) -> AsyncGenerator[AsyncSession, None]:
    """
    Asynchronous context manager yielding a managed database session.
    Commits on successful completion; rolls back on exception.
    """
    factory = get_session_factory(engine)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def close_db_engine(engine: Optional[AsyncEngine] = None) -> None:
    """
    Closes and disposes the async engine and its connection pools.
    """
    global _global_engine, _global_session_factory
    target_engine = engine or _global_engine
    if target_engine is not None:
        await target_engine.dispose()
        if target_engine is _global_engine:
            _global_engine = None
            _global_session_factory = None
