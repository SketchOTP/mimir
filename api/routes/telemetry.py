"""Telemetry API endpoints (P9).

Provides access to:
  - Cognitive metrics snapshots
  - Retrieval quality analytics
  - Procedural effectiveness analytics
  - Confidence drift detection
  - Memory heatmaps
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import UserContext, get_current_user
from storage.database import get_session

router = APIRouter(prefix="/telemetry", tags=["telemetry"])

# Short-path aliases for provider endpoints (no /telemetry prefix)
providers_router = APIRouter(prefix="/providers", tags=["telemetry"])


@router.get("/snapshot")
async def get_telemetry_snapshot(
    project: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    """Return the latest telemetry snapshot for all cognitive metrics."""
    from telemetry.cognition_metrics import get_latest_snapshot
    latest = await get_latest_snapshot(session, project=project)
    return {"ok": True, "metrics": latest, "project": project}


@router.post("/snapshot/compute")
async def compute_telemetry_snapshot(
    project: str | None = Query(None),
    period: str = Query("daily"),
    window_hours: int = Query(24),
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    """Trigger an immediate telemetry snapshot computation."""
    from telemetry.cognition_metrics import compute_snapshot
    metrics = await compute_snapshot(
        session, project=project, period=period, window_hours=window_hours
    )
    return {"ok": True, "metrics": metrics, "period": period}


@router.get("/metrics/{metric_name}/history")
async def get_metric_history(
    metric_name: str,
    project: str | None = Query(None),
    limit: int = Query(30, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    """Return historical values for a specific metric."""
    from telemetry.cognition_metrics import get_recent_snapshots
    history = await get_recent_snapshots(session, metric_name, project=project, limit=limit)
    return {"ok": True, "metric_name": metric_name, "history": history}


@router.get("/retrieval/stats")
async def get_retrieval_stats(
    project: str | None = Query(None),
    window_hours: int = Query(24, ge=1, le=720),
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    """Aggregate retrieval session statistics for the given window."""
    from telemetry.retrieval_analytics import get_retrieval_session_stats
    stats = await get_retrieval_session_stats(session, project=project, window_hours=window_hours)
    return {"ok": True, "stats": stats}


@router.get("/retrieval/heatmap")
async def get_retrieval_heatmap(
    project: str | None = Query(None),
    window_days: int = Query(30, ge=1, le=365),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    """Return memory usage heatmap — most/least useful memories."""
    from telemetry.retrieval_analytics import get_memory_heatmap
    heatmap = await get_memory_heatmap(
        session, project=project, window_days=window_days, limit=limit
    )
    return {"ok": True, "heatmap": heatmap}


@router.get("/procedural/effectiveness")
async def get_procedural_effectiveness(
    project: str | None = Query(None),
    min_retrievals: int = Query(1, ge=0),
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    """Return effectiveness metrics for all procedural memories."""
    from telemetry.procedural_analytics import get_all_procedural_effectiveness
    results = await get_all_procedural_effectiveness(
        session, project=project, min_retrievals=min_retrievals
    )
    return {"ok": True, "procedural_memories": results, "count": len(results)}


@router.get("/drift/detect")
async def detect_drift(
    project: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    """Detect memories showing confidence drift."""
    from telemetry.procedural_analytics import detect_confidence_drift
    candidates = await detect_confidence_drift(session, project=project)
    return {"ok": True, "drift_candidates": candidates, "count": len(candidates)}


@router.post("/drift/apply-decay")
async def apply_drift_decay(
    project: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    """Detect drift and apply conservative trust decay to confirmed candidates."""
    from telemetry.procedural_analytics import detect_confidence_drift, apply_drift_trust_decay
    candidates = await detect_confidence_drift(session, project=project)
    decayed = await apply_drift_trust_decay(session, candidates)
    return {
        "ok": True,
        "candidates_found": len(candidates),
        "memories_decayed": decayed,
    }


# ─── P10: Provider stats and adaptive retrieval analytics ─────────────────────

@router.get("/providers/stats")
async def get_provider_stats_all(
    project: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    """Return all accumulated provider stats rows."""
    from retrieval.provider_stats import get_all_provider_stats
    stats = await get_all_provider_stats(session, project=project)
    return {"ok": True, "provider_stats": stats, "count": len(stats)}


@router.post("/providers/aggregate")
async def aggregate_providers(
    project: str | None = Query(None),
    window_hours: int = Query(48, ge=1, le=720),
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    """Trigger immediate provider stats aggregation from recent sessions."""
    from retrieval.provider_stats import aggregate_provider_stats
    result = await aggregate_provider_stats(session, project=project, window_hours=window_hours)
    return {"ok": True, **result}


@router.get("/providers/drift")
async def get_provider_drift(
    project: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    """Return providers currently flagged for drift."""
    from storage.models import ProviderStats
    from sqlalchemy import select
    q = select(ProviderStats).where(ProviderStats.drift_flagged.is_(True))
    if project is not None:
        q = q.where(ProviderStats.project == project)
    result = await session.execute(q)
    rows = result.scalars().all()
    return {
        "ok": True,
        "drifting_providers": [
            {
                "provider_name": r.provider_name,
                "task_category": r.task_category,
                "usefulness_rate": r.usefulness_rate,
                "harmful_rate": r.harmful_rate,
                "weight_current": r.weight_current,
                "drift_reason": r.drift_reason,
                "drift_detected_at": r.drift_detected_at.isoformat() if r.drift_detected_at else None,
            }
            for r in rows
        ],
        "count": len(rows),
    }


# ── Short-path aliases (mounted at /api/providers/...) ───────────────────────

@providers_router.get("/stats")
async def _providers_stats_alias(
    project: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    return await get_provider_stats_all(project=project, session=session, current_user=current_user)


@providers_router.post("/aggregate")
async def _providers_aggregate_alias(
    project: str | None = Query(None),
    window_hours: int = Query(48, ge=1, le=720),
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    return await aggregate_providers(project=project, window_hours=window_hours, session=session, current_user=current_user)


@providers_router.get("/drift")
async def _providers_drift_alias(
    project: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    return await get_provider_drift(project=project, session=session, current_user=current_user)
