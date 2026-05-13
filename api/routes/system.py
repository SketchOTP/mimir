"""System observability endpoints: metrics, status, jobs, readiness."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import UserContext, get_current_user
from mimir.config import get_settings
from storage.database import get_session, healthcheck as db_healthcheck
from storage.models import Memory, MetricRecord, ApprovalRequest, Rollback
from storage import vector_store
from metrics.metrics_engine import get_dashboard_metrics, get_history, METRIC_NAMES
from worker.tasks import get_running_jobs

router = APIRouter(prefix="/system", tags=["system"])
metrics_router = APIRouter(tags=["system"])


def _get_migration_revision() -> str | None:
    """Read the current Alembic migration revision directly from the DB file."""
    settings = get_settings()
    db_path = settings.data_dir / "mimir.db"
    if not db_path.exists():
        return None
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("SELECT version_num FROM alembic_version LIMIT 1")
        row = cur.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def _get_fts_status() -> dict:
    """Check whether the FTS5 virtual table is present and has rows."""
    settings = get_settings()
    db_path = settings.data_dir / "mimir.db"
    if not db_path.exists():
        return {"status": "no_db"}
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM memory_fts")
        count = cur.fetchone()[0]
        conn.close()
        return {"status": "ok", "rows": count}
    except Exception as exc:
        return {"status": "unavailable", "error": str(exc)}


def _get_last_report(report_path: str) -> dict | None:
    p = Path(report_path)
    if not p.exists():
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return None


@router.get("/status")
async def system_status(
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    """Overall system health and component status."""
    db_health = await db_healthcheck()

    # Vector store status
    try:
        vec_counts = {layer: vector_store.count(layer)
                      for layer in ("episodic", "semantic", "procedural", "working")}
        vec_status = "ok"
    except Exception as exc:
        vec_counts = {}
        vec_status = f"error: {exc}"

    # Memory counts by layer
    mem_counts: dict[str, int] = {}
    for layer in ("episodic", "semantic", "procedural", "working"):
        result = await session.execute(
            select(func.count(Memory.id)).where(
                Memory.layer == layer, Memory.deleted_at.is_(None)
            )
        )
        mem_counts[layer] = result.scalar_one()

    # Pending approvals
    pending_q = await session.execute(
        select(func.count(ApprovalRequest.id)).where(ApprovalRequest.status == "pending")
    )
    pending_approvals = pending_q.scalar_one()

    # Rollback count
    rollback_q = await session.execute(select(func.count(Rollback.id)))
    rollback_count = rollback_q.scalar_one()

    # Migration revision
    migration_revision = _get_migration_revision()

    # FTS status
    fts_status = _get_fts_status()

    # Last eval / release gate reports
    last_eval = _get_last_report("reports/evals/latest.json")
    last_gate = _get_last_report("reports/gate/latest.json")

    return {
        "status": "ok" if db_health["status"] == "ok" and vec_status == "ok" else "degraded",
        "components": {
            "database": {**db_health, "migration_revision": migration_revision},
            "vector_store": {"status": vec_status, "counts": vec_counts},
            "fts": fts_status,
            "worker": {"running_jobs": get_running_jobs()},
        },
        "memory": {
            "counts_by_layer": mem_counts,
            "total": sum(mem_counts.values()),
        },
        "pending_approvals": pending_approvals,
        "rollback_count": rollback_count,
        "last_eval": {
            "passed": last_eval.get("passed") if last_eval else None,
            "suite_count": len(last_eval.get("suites", [])) if last_eval else None,
            "created_at": last_eval.get("created_at") if last_eval else None,
        } if last_eval else None,
        "last_gate": {
            "passed": last_gate.get("passed") if last_gate else None,
            "created_at": last_gate.get("created_at") if last_gate else None,
        } if last_gate else None,
    }


@router.get("/jobs")
async def system_jobs(
    current_user: UserContext = Depends(get_current_user),
):
    """Current background worker job status."""
    running = get_running_jobs()
    return {
        "running": running,
        "running_count": len(running),
    }


async def _metrics_response(
    session: AsyncSession,
    name: str | None,
    days: int,
    project: str | None,
) -> dict:
    if name:
        history = await get_history(session, name, project=project, days=days)
        return {
            "metric": name,
            "days": days,
            "points": [
                {"value": r.value, "recorded_at": r.recorded_at.isoformat(), "period": r.period}
                for r in history
            ],
        }
    latest: dict[str, float | None] = {}
    for metric_name in METRIC_NAMES:
        q = select(MetricRecord).where(MetricRecord.name == metric_name)
        if project:
            q = q.where(MetricRecord.project == project)
        q = q.order_by(MetricRecord.recorded_at.desc()).limit(1)
        result = await session.execute(q)
        rec = result.scalars().first()
        latest[metric_name] = rec.value if rec else None
    return {"metrics": latest, "available_names": METRIC_NAMES}


def _assert_mutation_enabled() -> None:
    """Raise 403 if system mutation endpoints are disabled."""
    from fastapi import HTTPException
    if not get_settings().enable_system_mutation_endpoints:
        raise HTTPException(
            status_code=403,
            detail=(
                "System mutation endpoints are disabled. "
                "Set MIMIR_ENABLE_SYSTEM_MUTATION_ENDPOINTS=true to enable."
            ),
        )


@router.post("/consolidate")
async def trigger_consolidation(
    project: str | None = None,
    current_user: UserContext = Depends(get_current_user),
):
    """Trigger an immediate consolidation pass (dreaming layer). Requires MIMIR_ENABLE_SYSTEM_MUTATION_ENDPOINTS=true."""
    _assert_mutation_enabled()
    from worker.tasks import run_consolidation_pass
    result = await run_consolidation_pass(project=project)
    return {"ok": True, "result": result}


@router.post("/reflect")
async def trigger_reflection(
    project: str | None = None,
    current_user: UserContext = Depends(get_current_user),
):
    """Trigger an immediate reflection pass. Requires MIMIR_ENABLE_SYSTEM_MUTATION_ENDPOINTS=true."""
    _assert_mutation_enabled()
    from worker.tasks import run_reflection_pass
    result = await run_reflection_pass(project=project)
    return {"ok": True, "result": result}


@router.post("/lifecycle")
async def trigger_lifecycle(
    current_user: UserContext = Depends(get_current_user),
):
    """Trigger an immediate lifecycle pass. Requires MIMIR_ENABLE_SYSTEM_MUTATION_ENDPOINTS=true."""
    _assert_mutation_enabled()
    from worker.tasks import run_lifecycle_pass
    result = await run_lifecycle_pass()
    return {"ok": True, "result": result}


@router.get("/metrics")
async def system_metrics(
    name: str | None = None,
    days: int = 7,
    project: str | None = None,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    """Metric history for one or all tracked metrics (also available at /api/metrics)."""
    return await _metrics_response(session, name, days, project)


@metrics_router.get("/metrics")
async def top_level_metrics(
    name: str | None = None,
    days: int = 7,
    project: str | None = None,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    """GET /api/metrics — top-level metrics endpoint."""
    return await _metrics_response(session, name, days, project)


@router.get("/readiness")
async def system_readiness(
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    """Deep readiness check — verifies DB, vector store, migrations, and FTS are all operational.

    Returns HTTP 200 if all checks pass, 503 if any critical component is down.
    Intended for load balancer / k8s readiness probes (behind auth).
    """
    from fastapi import HTTPException
    checks: dict[str, dict] = {}
    ready = True

    # DB connectivity
    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = {"ok": True}
    except Exception as exc:
        checks["database"] = {"ok": False, "error": str(exc)}
        ready = False

    # Migration version
    migration_revision = _get_migration_revision()
    checks["migration"] = {"ok": migration_revision is not None, "revision": migration_revision}
    if migration_revision is None:
        ready = False

    # Vector store
    try:
        vector_store.count("semantic")
        checks["vector_store"] = {"ok": True}
    except Exception as exc:
        checks["vector_store"] = {"ok": False, "error": str(exc)}
        ready = False

    # FTS (non-critical — missing FTS degrades keyword recall but doesn't block operation)
    fts = _get_fts_status()
    checks["fts"] = {"ok": fts["status"] == "ok", "status": fts["status"]}

    # Worker jobs (informational)
    running = get_running_jobs()
    checks["worker"] = {"ok": True, "running_jobs": running}

    if not ready:
        raise HTTPException(status_code=503, detail={"ready": False, "checks": checks})

    return {"ready": True, "checks": checks}
