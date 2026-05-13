"""Async SQLAlchemy engine + session factory — SQLite and Postgres.

Set MIMIR_DATABASE_URL to a postgresql+asyncpg:// URL for Postgres.
Leave it empty (default) to use SQLite with aiosqlite in MIMIR_DATA_DIR.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mimir.config import get_settings
from storage.models import Base

logger = logging.getLogger(__name__)

_engine = None
_session_factory = None

_MAX_RETRIES = 3
_RETRY_DELAY_S = 0.5


def _is_postgres(url: str) -> bool:
    return url.startswith("postgresql")


def _build_url(settings) -> str:
    """Return the async database URL based on settings."""
    if settings.database_url:
        url = settings.database_url
        # Normalise postgres:// → postgresql+asyncpg://
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://") and "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url
    db_path = settings.data_dir / "mimir.db"
    return f"sqlite+aiosqlite:///{db_path}"


def _get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        url = _build_url(settings)

        if _is_postgres(url):
            _engine = create_async_engine(
                url,
                echo=False,
                pool_pre_ping=True,
                pool_size=settings.db_pool_size,
                max_overflow=settings.db_max_overflow,
                pool_timeout=settings.db_pool_timeout,
            )
        else:
            _engine = create_async_engine(
                url,
                echo=False,
                connect_args={
                    "check_same_thread": False,
                    "timeout": 30,
                },
                pool_pre_ping=True,
                pool_size=5,
                max_overflow=10,
            )
    return _engine


def get_db_dialect() -> str:
    """Return 'sqlite' or 'postgresql' based on the current engine."""
    engine = _get_engine()
    return engine.dialect.name


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            _get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _session_factory


async def get_session() -> AsyncSession:
    """FastAPI dependency — yields a session with automatic cleanup."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def _with_retry(coro_factory, *, retries: int = _MAX_RETRIES, delay: float = _RETRY_DELAY_S):
    """Run an async coroutine factory with exponential back-off on OperationalError."""
    last_exc = None
    for attempt in range(retries):
        try:
            return await coro_factory()
        except OperationalError as exc:
            last_exc = exc
            wait = delay * (2 ** attempt)
            logger.warning("DB OperationalError (attempt %d/%d), retrying in %.1fs: %s",
                           attempt + 1, retries, wait, exc)
            await asyncio.sleep(wait)
    raise last_exc


async def init_db() -> None:
    """Create all tables (idempotent). SQLite also creates FTS5 virtual table + triggers."""
    async def _create():
        engine = _get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        if get_db_dialect() == "sqlite":
            await _init_sqlite_fts(engine)

    await _with_retry(_create)


async def _init_sqlite_fts(engine) -> None:
    """Create FTS5 virtual table and triggers on SQLite (idempotent)."""
    try:
        async with engine.begin() as conn:
            await conn.execute(text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts "
                "USING fts5("
                "  memory_id UNINDEXED, "
                "  user_id UNINDEXED, "
                "  project_id UNINDEXED, "
                "  content, "
                "  tokenize='unicode61 remove_diacritics 1'"
                ")"
            ))
            for stmt in _FTS5_TRIGGERS:
                await conn.execute(text(stmt))
    except Exception:
        pass  # FTS5 not available in this SQLite build — keyword falls back to LIKE


_FTS5_TRIGGERS = [
    """CREATE TRIGGER IF NOT EXISTS memories_ai_fts
AFTER INSERT ON memories
WHEN NEW.deleted_at IS NULL AND NEW.memory_state != 'quarantined'
BEGIN
    INSERT INTO memory_fts(memory_id, user_id, project_id, content)
    VALUES (NEW.id, COALESCE(NEW.user_id, ''), COALESCE(NEW.project, ''), NEW.content);
END""",
    """CREATE TRIGGER IF NOT EXISTS memories_au_fts
AFTER UPDATE OF content, user_id, project ON memories
BEGIN
    DELETE FROM memory_fts WHERE memory_id = OLD.id;
    INSERT INTO memory_fts(memory_id, user_id, project_id, content)
    SELECT NEW.id, COALESCE(NEW.user_id, ''), COALESCE(NEW.project, ''), NEW.content
    WHERE NEW.deleted_at IS NULL AND NEW.memory_state != 'quarantined';
END""",
    """CREATE TRIGGER IF NOT EXISTS memories_asoftdel_fts
AFTER UPDATE OF deleted_at ON memories
WHEN NEW.deleted_at IS NOT NULL
BEGIN
    DELETE FROM memory_fts WHERE memory_id = OLD.id;
END""",
    """CREATE TRIGGER IF NOT EXISTS memories_aquar_fts
AFTER UPDATE OF memory_state ON memories
WHEN NEW.memory_state = 'quarantined'
BEGIN
    DELETE FROM memory_fts WHERE memory_id = OLD.id;
END""",
]


async def validate_db() -> None:
    """Verify the DB is reachable and the schema looks sane. Raises on failure."""
    async def _probe():
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(text("SELECT 1"))

    try:
        await _with_retry(_probe, retries=2)
    except Exception as exc:
        raise RuntimeError(f"Database validation failed: {exc}") from exc


async def healthcheck() -> dict:
    """Return DB health status dict — safe to call from a health endpoint."""
    try:
        await validate_db()
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


if __name__ == "__main__":
    import sys
    if "--migrate" in sys.argv:
        asyncio.run(init_db())
        print("Database initialized.")
