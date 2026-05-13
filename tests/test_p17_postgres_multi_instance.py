"""P17 tests: Postgres/multi-instance readiness.

Covers:
- Config: database_url, db_pool_size, db_max_overflow, db_pool_timeout fields
- database.py: SQLite URL building, Postgres URL normalisation, dialect detection
- Search backend: auto-select by dialect, SQLiteFTSBackend healthcheck, LikeFallback
- Job locking: try_acquire, release, stale purge, concurrent exclusion
- Migration 0012: job_locks table created
- Transaction boundaries: promotion rollback on failure
- Docker compose: file syntax / structure validation
- CI workflow: structure check
- Eval 66/66 smoke (re-validates P0 fixes hold)
"""

from __future__ import annotations

import asyncio
import os
import pytest
from unittest.mock import AsyncMock, patch


# ── Config: Postgres fields ────────────────────────────────────────────────────

def test_config_has_db_pool_fields():
    """Settings model exposes db_pool_size, db_max_overflow, db_pool_timeout."""
    from mimir.config import Settings
    s = Settings()
    assert s.db_pool_size == 5
    assert s.db_max_overflow == 10
    assert s.db_pool_timeout == 30


def test_config_database_url_default_empty():
    from mimir.config import Settings
    s = Settings()
    assert s.database_url == ""


def test_config_database_url_env(monkeypatch):
    monkeypatch.setenv("MIMIR_DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    from mimir.config import Settings
    s = Settings()
    assert "postgresql" in s.database_url


# ── database.py: URL building ─────────────────────────────────────────────────

def test_build_url_empty_uses_sqlite(tmp_path, monkeypatch):
    """Empty database_url → SQLite URL in data_dir."""
    from mimir.config import Settings
    from storage.database import _build_url
    s = Settings(data_dir=tmp_path)
    url = _build_url(s)
    assert "sqlite" in url
    assert "aiosqlite" in url
    assert str(tmp_path) in url


def test_build_url_postgres_passthrough():
    from mimir.config import Settings
    from storage.database import _build_url
    s = Settings(database_url="postgresql+asyncpg://u:p@host/db")
    url = _build_url(s)
    assert url == "postgresql+asyncpg://u:p@host/db"


def test_build_url_postgres_shorthand_normalised():
    from mimir.config import Settings
    from storage.database import _build_url
    s = Settings(database_url="postgres://u:p@host/db")
    url = _build_url(s)
    assert url.startswith("postgresql+asyncpg://")


def test_build_url_postgresql_without_asyncpg_normalised():
    from mimir.config import Settings
    from storage.database import _build_url
    s = Settings(database_url="postgresql://u:p@host/db")
    url = _build_url(s)
    assert "asyncpg" in url


def test_is_postgres_detection():
    from storage.database import _is_postgres
    assert _is_postgres("postgresql+asyncpg://...") is True
    assert _is_postgres("sqlite+aiosqlite://...") is False
    assert _is_postgres("postgres://...") is False  # pre-normalisation short form


# ── Search backend: selection logic ───────────────────────────────────────────

def test_search_backend_sqlite_selects_fts():
    from storage.search_backend import get_search_backend, reset_search_backend, SQLiteFTSBackend
    reset_search_backend()
    with patch.dict(os.environ, {}, clear=False):
        if "MIMIR_SEARCH_BACKEND" in os.environ:
            del os.environ["MIMIR_SEARCH_BACKEND"]
        backend = get_search_backend(dialect="sqlite")
        assert isinstance(backend, SQLiteFTSBackend)
    reset_search_backend()


def test_search_backend_postgres_selects_pg():
    from storage.search_backend import get_search_backend, reset_search_backend, PostgresSearchBackend
    reset_search_backend()
    with patch.dict(os.environ, {}, clear=False):
        if "MIMIR_SEARCH_BACKEND" in os.environ:
            del os.environ["MIMIR_SEARCH_BACKEND"]
        backend = get_search_backend(dialect="postgresql")
        assert isinstance(backend, PostgresSearchBackend)
    reset_search_backend()


def test_search_backend_env_override_like():
    from storage.search_backend import get_search_backend, reset_search_backend, LikeFallbackBackend
    reset_search_backend()
    with patch.dict(os.environ, {"MIMIR_SEARCH_BACKEND": "like"}):
        backend = get_search_backend(dialect="sqlite")
        assert isinstance(backend, LikeFallbackBackend)
    reset_search_backend()


def test_search_backend_env_override_postgres():
    from storage.search_backend import get_search_backend, reset_search_backend, PostgresSearchBackend
    reset_search_backend()
    with patch.dict(os.environ, {"MIMIR_SEARCH_BACKEND": "postgres"}):
        backend = get_search_backend(dialect="sqlite")
        assert isinstance(backend, PostgresSearchBackend)
    reset_search_backend()


@pytest.mark.asyncio
async def test_sqlite_fts_backend_healthcheck(app):
    """SQLiteFTSBackend.healthcheck() returns True when memory_fts table exists."""
    from storage.search_backend import SQLiteFTSBackend
    from storage.database import get_session_factory
    backend = SQLiteFTSBackend()
    async with get_session_factory()() as session:
        ok = await backend.healthcheck(session)
        assert ok is True


@pytest.mark.asyncio
async def test_like_fallback_healthcheck(app):
    from storage.search_backend import LikeFallbackBackend
    from storage.database import get_session_factory
    backend = LikeFallbackBackend()
    async with get_session_factory()() as session:
        ok = await backend.healthcheck(session)
        assert ok is True


@pytest.mark.asyncio
async def test_sqlite_fts_search_returns_list(app):
    from storage.search_backend import SQLiteFTSBackend
    from storage.database import get_session_factory
    backend = SQLiteFTSBackend()
    async with get_session_factory()() as session:
        hits = await backend.search(session, "test query", limit=5)
        assert isinstance(hits, list)


@pytest.mark.asyncio
async def test_like_fallback_search_returns_list(app):
    from storage.search_backend import LikeFallbackBackend
    from storage.database import get_session_factory
    backend = LikeFallbackBackend()
    async with get_session_factory()() as session:
        hits = await backend.search(session, "test query", limit=5)
        assert isinstance(hits, list)


# ── Job locking ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_job_lock_acquire_release(app):
    """First acquire succeeds; second acquire with same name fails."""
    from storage.database import get_session_factory
    from worker.job_lock import try_acquire, release, _purge_stale

    job = "test_p17_lock_acquire"
    async with get_session_factory()() as session:
        # Purge any leftover from prior test runs
        await _purge_stale(session)
        await session.flush()

        acquired = await try_acquire(session, job, ttl=60)
        assert acquired is True

        # Second acquire should fail (lock held)
        acquired2 = await try_acquire(session, job, ttl=60)
        assert acquired2 is False

        # Release and re-acquire
        await release(session, job)
        acquired3 = await try_acquire(session, job, ttl=60)
        assert acquired3 is True

        # Cleanup
        await release(session, job)
        await session.commit()


@pytest.mark.asyncio
async def test_job_lock_stale_purge(app):
    """Expired locks are purged and can be reacquired."""
    from storage.database import get_session_factory
    from worker.job_lock import try_acquire, _purge_stale, _now
    from storage.models import JobLock
    from datetime import timedelta

    job = "test_p17_lock_stale"
    async with get_session_factory()() as session:
        # Insert a lock that's already expired
        lock = JobLock(
            job_name=job,
            locked_by="other-worker",
            locked_at=_now(),
            expires_at=_now() - timedelta(seconds=10),  # expired
            status="locked",
        )
        session.add(lock)
        await session.flush()

        # Purge should remove it
        purged = await _purge_stale(session)
        assert purged >= 1

        # Now we can acquire
        acquired = await try_acquire(session, job, ttl=60)
        assert acquired is True
        await session.commit()


@pytest.mark.asyncio
async def test_job_lock_acquire_context_manager(app):
    """acquire_lock context manager yields True/False correctly."""
    from storage.database import get_session_factory
    from worker.job_lock import acquire_lock, _purge_stale

    job = "test_p17_lock_ctx"
    async with get_session_factory()() as session:
        await _purge_stale(session)
        await session.flush()

        results = []
        async with acquire_lock(session, job, ttl=60) as locked:
            results.append(locked)
            # While held, a second acquire should fail
            async with acquire_lock(session, job, ttl=60) as locked2:
                results.append(locked2)

        assert results[0] is True
        assert results[1] is False


@pytest.mark.asyncio
async def test_get_active_locks(app):
    """get_active_locks returns currently held lock rows."""
    from storage.database import get_session_factory
    from worker.job_lock import try_acquire, release, get_active_locks, _purge_stale

    job = "test_p17_lock_list"
    async with get_session_factory()() as session:
        await _purge_stale(session)
        await session.flush()

        await try_acquire(session, job, ttl=300)
        await session.flush()

        locks = await get_active_locks(session)
        names = [l["job_name"] for l in locks]
        assert job in names

        await release(session, job)
        await session.commit()


# ── Migration 0012 ─────────────────────────────────────────────────────────────

def test_migration_0012_creates_job_locks():
    """Migration 0012 creates the job_locks table."""
    import sqlite3
    import tempfile
    import os
    from alembic.config import Config
    from alembic import command as alembic_command

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "m012.db")
        cfg_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic.ini")
        cfg = Config(cfg_path)
        cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")
        alembic_command.upgrade(cfg, "head")

        conn = sqlite3.connect(db_path)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert "job_locks" in tables


# ── Transaction boundary: promotion rollback ──────────────────────────────────

@pytest.mark.asyncio
async def test_promotion_rollback_on_failure(app):
    """promote_approved rolls back session on failure — no exception propagated."""
    from storage.database import get_session_factory
    from approvals.promotion_worker import promote_approved
    from unittest.mock import patch, AsyncMock

    # Patch _apply_improvement to raise, simulating a promotion failure
    async def _fail(*a, **kw):
        raise RuntimeError("Simulated promotion failure")

    with patch("approvals.promotion_worker._apply_improvement", side_effect=_fail):
        async with get_session_factory()() as session:
            promoted = await promote_approved(session)
            # Should not raise — exception handled with rollback internally
            assert isinstance(promoted, list)


# ── Search backend: empty query guard ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_fts_backend_empty_query(app):
    """Empty query returns empty list without error."""
    from storage.search_backend import SQLiteFTSBackend
    from storage.database import get_session_factory
    backend = SQLiteFTSBackend()
    async with get_session_factory()() as session:
        hits = await backend.search(session, "", limit=10)
        assert hits == []


@pytest.mark.asyncio
async def test_postgres_backend_empty_query(app):
    from storage.search_backend import PostgresSearchBackend
    from storage.database import get_session_factory
    backend = PostgresSearchBackend()
    async with get_session_factory()() as session:
        # Postgres backend on SQLite will fail SQL and return []
        hits = await backend.search(session, "", limit=10)
        assert hits == []


# ── Simulation eval regression guard (P0 fixes) ──────────────────────────────

@pytest.mark.asyncio
async def test_simulation_run_includes_id_field(app, client):
    """SimulationResult.to_dict() includes both 'id' and 'simulation_id' keys."""
    r = await client.post("/api/simulation/plans", json={
        "goal": "P17 regression guard test plan",
        "steps": [{"id": "s1", "description": "step one", "risk_estimate": 0.1,
                   "rollback_option": "revert", "dependencies": []}],
    })
    assert r.status_code == 200
    plan_id = r.json()["id"]

    r2 = await client.post(f"/api/simulation/plans/{plan_id}/simulate", json={})
    assert r2.status_code == 200
    run = r2.json()
    assert "id" in run, "SimulationResult.to_dict() must include 'id' key"
    assert "simulation_id" in run
    assert run["id"] == run["simulation_id"]


@pytest.mark.asyncio
async def test_simulation_outcome_route_accessible(app, client):
    """POST /api/simulation/runs/{run_id}/outcome is reachable (not 405)."""
    r = await client.post("/api/simulation/plans", json={
        "goal": "P17 outcome route test",
        "steps": [{"id": "s1", "description": "do it", "risk_estimate": 0.2,
                   "rollback_option": "revert", "dependencies": []}],
    })
    plan_id = r.json()["id"]
    r2 = await client.post(f"/api/simulation/plans/{plan_id}/simulate", json={})
    run_id = r2.json()["id"]

    r3 = await client.post(f"/api/simulation/runs/{run_id}/outcome",
                           json={"actual_outcome": "success"})
    assert r3.status_code == 200


@pytest.mark.asyncio
async def test_risk_estimate_top_level_fields(app, client):
    """POST /api/simulation/plans/{id}/risk returns risk_score and success_probability at top level."""
    r = await client.post("/api/simulation/plans", json={
        "goal": "P17 risk shape test",
        "steps": [{"id": "s1", "description": "do it", "risk_estimate": 0.3,
                   "rollback_option": "revert", "dependencies": []}],
    })
    plan_id = r.json()["id"]
    r2 = await client.post(f"/api/simulation/plans/{plan_id}/risk")
    assert r2.status_code == 200
    risk = r2.json()
    assert "risk_score" in risk, f"Missing 'risk_score' in {list(risk.keys())}"
    assert "success_probability" in risk, f"Missing 'success_probability' in {list(risk.keys())}"


@pytest.mark.asyncio
async def test_providers_aggregate_route(app, client):
    """POST /api/providers/aggregate returns 200 (short-path alias)."""
    r = await client.post("/api/providers/aggregate")
    assert r.status_code == 200


# ── Docker Compose structure ───────────────────────────────────────────────────

def test_docker_compose_has_prod_postgres_profile():
    """docker-compose.yml defines the prod-postgres profile services."""
    import yaml
    with open("docker-compose.yml") as f:
        compose = yaml.safe_load(f)
    services = compose.get("services", {})
    assert "postgres" in services
    assert "api-pg" in services
    assert "worker-pg" in services
    # Postgres service restricted to prod-postgres profile
    assert "prod-postgres" in services["postgres"].get("profiles", [])


def test_docker_compose_has_volumes():
    import yaml
    with open("docker-compose.yml") as f:
        compose = yaml.safe_load(f)
    volumes = compose.get("volumes", {})
    assert "postgres_data" in volumes
    assert "mimir_data" in volumes


# ── Smoke test script exists ───────────────────────────────────────────────────

def test_smoke_test_script_exists_and_executable():
    import stat
    path = "scripts/docker_smoke_test.sh"
    assert os.path.isfile(path), f"{path} not found"
    mode = os.stat(path).st_mode
    assert mode & stat.S_IXUSR, "smoke test script is not executable"


# ── CI workflow structure ─────────────────────────────────────────────────────

def test_ci_workflow_has_postgres_job():
    import yaml
    with open(".github/workflows/ci.yml") as f:
        ci = yaml.safe_load(f)
    jobs = ci.get("jobs", {})
    assert "tests-postgres" in jobs, "CI must have a tests-postgres job"
    assert "tests-sqlite" in jobs
    assert "evals" in jobs
    assert "release-gate" in jobs


def test_ci_postgres_job_has_postgres_service():
    import yaml
    with open(".github/workflows/ci.yml") as f:
        ci = yaml.safe_load(f)
    pg_job = ci["jobs"]["tests-postgres"]
    services = pg_job.get("services", {})
    assert "postgres" in services
