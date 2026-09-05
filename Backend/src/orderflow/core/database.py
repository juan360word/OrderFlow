"""Async database engine, session factory and the FastAPI session dependency.

Engine vs. session — the distinction that matters:

* The **engine** is created once per process. It owns the connection *pool*:
  a set of long-lived TCP connections to PostgreSQL. Opening a Postgres
  connection forks a backend process on the server, so it is expensive; the
  pool exists so a request never pays that cost.
* A **session** is created once per request (per unit of work). It borrows a
  connection from the pool, tracks the objects you loaded and the changes you
  made, and flushes them inside one transaction. It is *not* thread- or
  task-safe and must never be shared between concurrent requests.

Why async: this service spends almost all its wall-clock time waiting on the
network — Postgres, later Redis and SQS. With asyncpg, one worker process can
hold thousands of in-flight requests while they wait, instead of blocking an OS
thread per request. The cost is that any accidental blocking call (a sync
driver, `time.sleep`, a CPU-heavy loop) stalls the entire event loop.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from orderflow.core.config import Environment, Settings
from orderflow.core.logging import get_logger

logger = get_logger(__name__)


def create_engine(settings: Settings) -> AsyncEngine:
    """Build the process-wide async engine."""
    is_testing = settings.environment is Environment.TESTING

    engine_kwargs: dict[str, Any] = {
        "echo": settings.db_echo,
        "connect_args": {
            "server_settings": {
                "application_name": settings.app_name,
                "statement_timeout": str(settings.db_statement_timeout_ms),
                "lock_timeout": str(settings.db_lock_timeout_ms),
                "idle_in_transaction_session_timeout": "30000",
            },
            "statement_cache_size": 0,
        },
    }

    if is_testing:
        engine_kwargs["poolclass"] = NullPool
    else:
        engine_kwargs.update(
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout_seconds,
            pool_recycle=300,
            pool_pre_ping=True,
        )

    return create_async_engine(settings.database_url, **engine_kwargs)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build the session factory bound to an engine."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=True,
    )


class Database:
    """Owns the engine and session factory for one application instance.

    Bundled in an object rather than left as module-level globals so tests can
    stand up an isolated database without monkey-patching imports.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.engine: AsyncEngine = create_engine(settings)
        self.session_factory: async_sessionmaker[AsyncSession] = create_session_factory(self.engine)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Unit of work: commit on success, roll back on any exception.

        This is the transaction boundary for the whole application. Services
        never commit; they express intent and let this scope decide. That is
        what makes a multi-step operation (reserve stock + write an order)
        atomic without every service knowing about the others.
        """
        session = self.session_factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def check_connection(self) -> bool:
        """Liveness probe used by the readiness endpoint."""
        from sqlalchemy import text

        try:
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except (SQLAlchemyError, OSError) as exc:
            logger.error("database_healthcheck_failed", error_type=type(exc).__name__)
            return False
        return True

    async def dispose(self) -> None:
        """Close every pooled connection. Called on application shutdown."""
        await self.engine.dispose()
        logger.info("database_engine_disposed")
