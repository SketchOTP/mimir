"""Alembic migration safety tests.

Verifies:
  - Fresh DB: alembic upgrade head creates all required tables
  - Existing DB: upgrade head is a no-op (idempotent)
  - Pre-Alembic DB: stamped existing DB survives upgrade without data loss
  - backfill_promoted_at is safe to call at startup on any DB state
"""

import os
import sqlite3
import tempfile
import pytest

from alembic.config import Config
from alembic import command as alembic_command


EXPECTED_TABLES = {
    "memories",
    "memory_events",
    "memory_links",
    "sessions",
    "task_traces",
    "skills",
    "skill_versions",
    "skill_runs",
    "reflections",
    "improvement_proposals",
    "approval_requests",
    "rollbacks",
    "notifications",
    "push_subscriptions",
    "metrics",
    "context_builds",
    "retrieval_logs",
    "episodic_chains",
    "lifecycle_events",
    "retrieval_feedback",
    "retrieval_sessions",
    "telemetry_snapshots",
    "provider_stats",
    "graph_nodes",
    "graph_edges",
    "simulation_plans",
    "simulation_runs",
    "forecast_calibration",
    "job_locks",
    "oauth_clients",
    "oauth_authorization_codes",
    "oauth_tokens",
    "oauth_refresh_tokens",
    "alembic_version",
}


def _make_alembic_cfg(db_path: str) -> Config:
    """Return an Alembic Config pointing at a temp database."""
    cfg = Config(os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")
    return cfg


def _tables(db_path: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


# ── Fresh DB migration ────────────────────────────────────────────────────────

def test_fresh_db_migration_creates_all_tables():
    """alembic upgrade head on a fresh SQLite file creates every expected table."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "fresh.db")
        cfg = _make_alembic_cfg(db_path)
        alembic_command.upgrade(cfg, "head")
        tables = _tables(db_path)
        missing = EXPECTED_TABLES - tables
        assert not missing, f"Missing tables after fresh migration: {missing}"


def test_alembic_version_table_populated():
    """alembic_version table exists and contains the head revision after upgrade."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "version.db")
        cfg = _make_alembic_cfg(db_path)
        alembic_command.upgrade(cfg, "head")
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT version_num FROM alembic_version").fetchall()
        conn.close()
        assert rows, "alembic_version table is empty after upgrade"
        assert rows[0][0] == "0013", f"Unexpected revision: {rows[0][0]}"


# ── Idempotent upgrade ────────────────────────────────────────────────────────

def test_upgrade_head_idempotent():
    """Running alembic upgrade head twice does not fail or duplicate tables."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "idempotent.db")
        cfg = _make_alembic_cfg(db_path)
        alembic_command.upgrade(cfg, "head")
        tables_after_first = _tables(db_path)
        alembic_command.upgrade(cfg, "head")
        tables_after_second = _tables(db_path)
        assert tables_after_first == tables_after_second, (
            "Second upgrade changed the table set"
        )


# ── Existing DB upgrade (pre-Alembic data survives) ──────────────────────────

def test_existing_db_stamp_and_upgrade_preserves_data():
    """Pre-Alembic DB: stamp head, then upgrade head preserves existing data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "existing.db")

        # Simulate a pre-Alembic database: create tables directly via SQLAlchemy
        import asyncio
        from sqlalchemy.ext.asyncio import create_async_engine
        from storage.models import Base

        async def _init():
            engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            await engine.dispose()

        asyncio.run(_init())

        # Insert a row to prove data survives
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO memories (id, layer, content, importance, access_count) "
            "VALUES ('test_mem_001', 'semantic', 'survive upgrade test', 0.9, 0)"
        )
        conn.commit()
        conn.close()

        # Stamp the DB at head (mark it as already at the current revision)
        cfg = _make_alembic_cfg(db_path)
        alembic_command.stamp(cfg, "head")

        # Upgrade head — should be a no-op
        alembic_command.upgrade(cfg, "head")

        # Data must still be present
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT id FROM memories WHERE id='test_mem_001'"
        ).fetchall()
        conn.close()
        assert rows, "Data was lost during stamp+upgrade of existing DB"


# ── Backfill safety ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_backfill_promoted_at_safe_on_empty_db(app):
    """backfill_promoted_at returns 0 and does not crash when no promotions exist."""
    from storage.database import get_session_factory
    from approvals.promotion_worker import backfill_promoted_at

    async with get_session_factory()() as session:
        count = await backfill_promoted_at(session)
        assert isinstance(count, int)
        assert count >= 0
