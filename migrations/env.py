"""Alembic environment for Mimir — supports async SQLAlchemy + aiosqlite/asyncpg."""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage.models import Base  # noqa: E402

alembic_cfg = context.config

if alembic_cfg.config_file_name is not None:
    fileConfig(alembic_cfg.config_file_name)

target_metadata = Base.metadata

_DEFAULT_INI_URL = "sqlite+aiosqlite:///./data/mimir.db"


def _get_url() -> str:
    """Return the database URL, preferring env config over alembic.ini."""
    cfg_url = alembic_cfg.get_main_option("sqlalchemy.url") or ""
    # If CLI or tests set a non-default URL, use it as-is
    if cfg_url and cfg_url != _DEFAULT_INI_URL:
        return cfg_url
    # Derive from Mimir config (respects MIMIR_DATABASE_URL and MIMIR_DATA_DIR)
    try:
        from storage.database import _build_url
        from mimir.config import get_settings
        url = _build_url(get_settings())
        return url
    except Exception:
        return cfg_url or _DEFAULT_INI_URL


def run_migrations_offline() -> None:
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    is_sqlite = connection.dialect.name == "sqlite"
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=is_sqlite,  # batch mode only needed for SQLite ALTER TABLE
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    cfg_section = alembic_cfg.get_section(alembic_cfg.config_ini_section) or {}
    cfg_section["sqlalchemy.url"] = _get_url()

    connectable = async_engine_from_config(
        cfg_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
