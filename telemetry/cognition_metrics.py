"""Cognitive telemetry — compute and persist system-wide operational metrics.

Tracks:
  - retrieval usefulness rate
  - harmful retrieval rate
  - procedural success rate
  - retrieval-to-success correlation
  - memory trust distribution
  - rollback correlation
  - token efficiency trends
  - memory state distribution
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, UTC

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import (
    Memory, RetrievalFeedback, RetrievalSession, TelemetrySnapshot,
    Rollback, LifecycleEvent,
)
from memory.trust import MemoryState

logger = logging.getLogger(__name__)


async def compute_snapshot(
    session: AsyncSession,
    *,
    project: str | None = None,
    period: str = "daily",
    window_hours: int = 24,
) -> dict[str, float]:
    """Compute all cognitive metrics and persist as TelemetrySnapshot rows.

    Returns dict of metric_name -> value for the caller to inspect.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
    metrics: dict[str, float] = {}

    # ── 1. Retrieval usefulness rate ──────────────────────────────────────────
    fb_result = await session.execute(
        select(RetrievalFeedback).where(RetrievalFeedback.created_at >= cutoff)
    )
    feedbacks = fb_result.scalars().all()
    if feedbacks:
        useful = sum(1 for f in feedbacks if f.outcome == "success")
        metrics["retrieval_usefulness_rate"] = round(useful / len(feedbacks), 4)
        harmful = sum(1 for f in feedbacks if f.outcome == "harmful")
        metrics["harmful_retrieval_rate"] = round(harmful / len(feedbacks), 4)
        irrelevant = sum(1 for f in feedbacks if f.outcome == "irrelevant")
        metrics["irrelevant_retrieval_rate"] = round(irrelevant / len(feedbacks), 4)
    else:
        metrics["retrieval_usefulness_rate"] = 0.0
        metrics["harmful_retrieval_rate"] = 0.0
        metrics["irrelevant_retrieval_rate"] = 0.0

    # ── 2. Retrieval-to-success correlation (session-based) ───────────────────
    sess_result = await session.execute(
        select(RetrievalSession).where(
            RetrievalSession.created_at >= cutoff,
            RetrievalSession.task_outcome.isnot(None),
        )
    )
    retrieval_sessions = sess_result.scalars().all()
    if retrieval_sessions:
        success_sessions = [s for s in retrieval_sessions if s.task_outcome == "success"]
        metrics["retrieval_to_success_rate"] = round(
            len(success_sessions) / len(retrieval_sessions), 4
        )
        # Average quality scores for successful vs failed sessions
        successful_usefulness = [
            s.usefulness_score for s in success_sessions if s.usefulness_score is not None
        ]
        metrics["avg_usefulness_on_success"] = round(
            sum(successful_usefulness) / len(successful_usefulness), 4
        ) if successful_usefulness else 0.0
    else:
        metrics["retrieval_to_success_rate"] = 0.0
        metrics["avg_usefulness_on_success"] = 0.0

    # ── 3. Procedural success rate ────────────────────────────────────────────
    proc_result = await session.execute(
        select(Memory).where(
            Memory.layer == "procedural",
            Memory.deleted_at.is_(None),
            Memory.memory_state.notin_(list(MemoryState.BLOCKED)),
        )
    )
    proc_mems = proc_result.scalars().all()
    if proc_mems:
        total_succ = sum(m.successful_retrievals or 0 for m in proc_mems)
        total_fail = sum(m.failed_retrievals or 0 for m in proc_mems)
        total_retrievals = total_succ + total_fail
        metrics["procedural_success_rate"] = round(
            total_succ / total_retrievals, 4
        ) if total_retrievals > 0 else 0.0
        metrics["procedural_failure_rate"] = round(
            total_fail / total_retrievals, 4
        ) if total_retrievals > 0 else 0.0
        metrics["procedural_memory_count"] = float(len(proc_mems))
        avg_trust = sum(m.trust_score or 0.7 for m in proc_mems) / len(proc_mems)
        metrics["procedural_avg_trust"] = round(avg_trust, 4)
    else:
        metrics["procedural_success_rate"] = 0.0
        metrics["procedural_failure_rate"] = 0.0
        metrics["procedural_memory_count"] = 0.0
        metrics["procedural_avg_trust"] = 0.0

    # ── 4. Memory state distribution ─────────────────────────────────────────
    state_result = await session.execute(
        select(Memory.memory_state, func.count(Memory.id))
        .where(Memory.deleted_at.is_(None))
        .group_by(Memory.memory_state)
    )
    state_counts = dict(state_result.all())
    total_mems = sum(state_counts.values()) or 1
    for state in ["active", "aging", "stale", "archived", "quarantined", "contradicted"]:
        metrics[f"memory_state_{state}_pct"] = round(
            state_counts.get(state, 0) / total_mems, 4
        )
    metrics["total_memory_count"] = float(total_mems)

    # ── 5. Trust distribution ─────────────────────────────────────────────────
    trust_result = await session.execute(
        select(Memory.trust_score).where(
            Memory.deleted_at.is_(None),
            Memory.memory_state.notin_(list(MemoryState.BLOCKED)),
        )
    )
    trust_scores = [row[0] for row in trust_result.all() if row[0] is not None]
    if trust_scores:
        metrics["avg_trust_score"] = round(sum(trust_scores) / len(trust_scores), 4)
        metrics["high_trust_pct"] = round(
            sum(1 for t in trust_scores if t >= 0.8) / len(trust_scores), 4
        )
        metrics["low_trust_pct"] = round(
            sum(1 for t in trust_scores if t < 0.4) / len(trust_scores), 4
        )
    else:
        metrics["avg_trust_score"] = 0.0
        metrics["high_trust_pct"] = 0.0
        metrics["low_trust_pct"] = 0.0

    # ── 6. Rollback correlation ───────────────────────────────────────────────
    rollback_result = await session.execute(
        select(func.count(Rollback.id)).where(Rollback.created_at >= cutoff)
    )
    rollback_count = rollback_result.scalar() or 0
    metrics["rollback_count"] = float(rollback_count)
    if retrieval_sessions:
        sessions_with_rollback = sum(
            1 for s in retrieval_sessions if s.rollback_id is not None
        )
        metrics["rollback_correlation"] = round(
            sessions_with_rollback / len(retrieval_sessions), 4
        )
    else:
        metrics["rollback_correlation"] = 0.0

    # ── 7. Token efficiency ───────────────────────────────────────────────────
    scored_sessions = [
        s for s in retrieval_sessions if s.token_efficiency_score is not None
    ]
    if scored_sessions:
        avg_token_eff = sum(s.token_efficiency_score for s in scored_sessions) / len(scored_sessions)
        metrics["avg_token_efficiency"] = round(avg_token_eff, 4)
    else:
        metrics["avg_token_efficiency"] = 0.0

    # ── 8. Persist all metrics ────────────────────────────────────────────────
    now = datetime.now(UTC)
    for name, value in metrics.items():
        session.add(TelemetrySnapshot(
            id=uuid.uuid4().hex,
            metric_name=name,
            metric_value=value,
            period=period,
            project=project,
            meta={"window_hours": window_hours, "computed_at": now.isoformat()},
        ))

    await session.commit()
    logger.info("telemetry: persisted %d metrics (period=%s)", len(metrics), period)
    return metrics


async def get_recent_snapshots(
    session: AsyncSession,
    metric_name: str,
    *,
    project: str | None = None,
    limit: int = 30,
) -> list[dict]:
    """Return recent snapshot history for a given metric."""
    filters = [TelemetrySnapshot.metric_name == metric_name]
    if project is not None:
        filters.append(TelemetrySnapshot.project == project)

    result = await session.execute(
        select(TelemetrySnapshot)
        .where(*filters)
        .order_by(TelemetrySnapshot.recorded_at.desc())
        .limit(limit)
    )
    rows = result.scalars().all()
    return [
        {
            "metric_name": r.metric_name,
            "value": r.metric_value,
            "period": r.period,
            "project": r.project,
            "recorded_at": r.recorded_at.isoformat() if r.recorded_at else None,
        }
        for r in rows
    ]


async def get_latest_snapshot(
    session: AsyncSession,
    *,
    project: str | None = None,
) -> dict[str, float]:
    """Return the most recent value for each metric (latest snapshot per name)."""
    # Get all distinct metric names
    names_result = await session.execute(
        select(TelemetrySnapshot.metric_name).distinct()
    )
    names = [row[0] for row in names_result.all()]

    latest: dict[str, float] = {}
    for name in names:
        filters = [TelemetrySnapshot.metric_name == name]
        if project is not None:
            filters.append(TelemetrySnapshot.project == project)
        row = await session.execute(
            select(TelemetrySnapshot)
            .where(*filters)
            .order_by(TelemetrySnapshot.recorded_at.desc())
            .limit(1)
        )
        snap = row.scalars().first()
        if snap:
            latest[name] = snap.metric_value

    return latest
