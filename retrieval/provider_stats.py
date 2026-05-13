"""Provider usefulness tracking and drift detection for P10.

Background worker that aggregates per-provider stats from recent RetrievalSession
records and updates ProviderStats rows.  Drift detection and weight decay are
conservative — no automatic disabling.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from retrieval.adaptive_weights import (
    ALL_PROVIDERS,
    _CATEGORY_BOOSTS,
    update_weight_from_stats,
)
from storage.models import ProviderStats, RetrievalSession

logger = logging.getLogger(__name__)

# Drift thresholds — conservative
_DRIFT_HARMFUL_RATE = 0.15    # >15% harmful retrievals → drift
_DRIFT_MIN_USEFULNESS = 0.25  # <25% useful → drift
_DRIFT_MIN_SESSIONS = 10      # need at least 10 sessions before flagging


def _check_drift(stats: ProviderStats) -> tuple[bool, str]:
    """Return (is_drifting, reason)."""
    if (stats.total_sessions or 0) < _DRIFT_MIN_SESSIONS:
        return False, ""
    if (stats.harmful_rate or 0.0) > _DRIFT_HARMFUL_RATE:
        return True, f"harmful_rate={stats.harmful_rate:.1%} > {_DRIFT_HARMFUL_RATE:.0%}"
    if (stats.usefulness_rate or 0.5) < _DRIFT_MIN_USEFULNESS:
        return True, f"usefulness_rate={stats.usefulness_rate:.1%} < {_DRIFT_MIN_USEFULNESS:.0%}"
    return False, ""


async def _get_or_create_stats(
    session: AsyncSession,
    provider_name: str,
    project: str | None,
    task_category: str | None,
) -> ProviderStats:
    """Fetch an existing ProviderStats row or create a new one."""
    q = select(ProviderStats).where(
        ProviderStats.provider_name == provider_name,
    )
    # NULL comparisons in SQLAlchemy
    if project is None:
        q = q.where(ProviderStats.project.is_(None))
    else:
        q = q.where(ProviderStats.project == project)
    if task_category is None:
        q = q.where(ProviderStats.task_category.is_(None))
    else:
        q = q.where(ProviderStats.task_category == task_category)

    result = await session.execute(q)
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    ps = ProviderStats(
        id=uuid.uuid4().hex,
        provider_name=provider_name,
        project=project,
        task_category=task_category,
    )
    session.add(ps)
    return ps


async def aggregate_provider_stats(
    session: AsyncSession,
    *,
    project: str | None = None,
    window_hours: int = 48,
) -> dict:
    """Aggregate provider stats from recent retrieval sessions.

    Only processes sessions that have `active_providers` set (i.e., created
    after migration 0008).  Returns a summary dict.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
    q = select(RetrievalSession).where(
        RetrievalSession.created_at >= cutoff,
        RetrievalSession.active_providers.isnot(None),
    )
    if project:
        q = q.where(RetrievalSession.project == project)

    result = await session.execute(q)
    sessions = result.scalars().all()

    if not sessions:
        return {"sessions_processed": 0, "stats_updated": 0, "drift_flagged": 0}

    # Accumulate raw counts per (provider, task_category)
    # Structure: {(provider, task_category): {total, useful, harmful, contributions}}
    accumulator: dict[tuple[str, str | None], dict] = {}

    for rs in sessions:
        providers = rs.active_providers or []
        category = rs.task_category  # may be None for old sessions
        contributions = rs.provider_contributions or {}
        outcome = rs.task_outcome

        for provider in providers:
            if provider not in ALL_PROVIDERS:
                continue
            key = (provider, category)
            if key not in accumulator:
                accumulator[key] = {
                    "total": 0,
                    "useful": 0,
                    "harmful": 0,
                    "memories": 0,
                    "efficiency_sum": 0.0,
                    "efficiency_count": 0,
                }
            acc = accumulator[key]
            acc["total"] += 1
            if outcome == "success" and not rs.has_harmful_outcome:
                acc["useful"] += 1
            if rs.has_harmful_outcome:
                acc["harmful"] += 1
            acc["memories"] += contributions.get(provider, 0)
            if rs.token_efficiency_score is not None:
                acc["efficiency_sum"] += rs.token_efficiency_score
                acc["efficiency_count"] += 1

    # Update ProviderStats rows
    stats_updated = 0
    drift_flagged = 0

    for (provider, category), acc in accumulator.items():
        ps = await _get_or_create_stats(session, provider, project, category)

        # Accumulate (additive)
        ps.total_sessions = (ps.total_sessions or 0) + acc["total"]
        ps.useful_sessions = (ps.useful_sessions or 0) + acc["useful"]
        ps.harmful_sessions = (ps.harmful_sessions or 0) + acc["harmful"]
        ps.total_memories_contributed = (ps.total_memories_contributed or 0) + acc["memories"]

        total = ps.total_sessions or 1
        ps.usefulness_rate = round(ps.useful_sessions / total, 4)
        ps.harmful_rate = round(ps.harmful_sessions / total, 4)

        if acc["efficiency_count"] > 0:
            new_eff = acc["efficiency_sum"] / acc["efficiency_count"]
            # Blend with existing (EMA)
            ps.avg_token_efficiency = round(
                0.7 * (ps.avg_token_efficiency or 0.5) + 0.3 * new_eff, 4
            )

        # Drift detection
        drifting, reason = _check_drift(ps)
        if drifting and not ps.drift_flagged:
            ps.drift_flagged = True
            ps.drift_reason = reason
            ps.drift_detected_at = datetime.now(UTC)
            drift_flagged += 1
        elif not drifting and ps.drift_flagged:
            # Recovery: clear drift flag if stats improved
            ps.drift_flagged = False
            ps.drift_reason = None

        # Adaptive weight update (slow, bounded)
        base_w = _CATEGORY_BOOSTS.get(category or "general", _CATEGORY_BOOSTS["general"]).get(provider, 1.0)
        ps.weight_current = update_weight_from_stats(
            ps.weight_current or 1.0,
            ps.usefulness_rate,
            base_w,
        )

        ps.last_updated_at = datetime.now(UTC)
        session.add(ps)
        stats_updated += 1

    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    logger.info(
        "Provider stats aggregated: sessions=%d stats_rows=%d drift_flagged=%d",
        len(sessions), stats_updated, drift_flagged,
    )
    return {
        "sessions_processed": len(sessions),
        "stats_updated": stats_updated,
        "drift_flagged": drift_flagged,
    }


async def get_provider_stats(
    session: AsyncSession,
    *,
    project: str | None = None,
    task_category: str | None = None,
) -> dict[str, ProviderStats | None]:
    """Load current ProviderStats for all providers matching criteria.

    Returns {provider_name: ProviderStats} for use in adaptive weight computation.
    """
    q = select(ProviderStats).where(ProviderStats.provider_name.in_(list(ALL_PROVIDERS)))
    if project is None:
        q = q.where(ProviderStats.project.is_(None))
    else:
        q = q.where(ProviderStats.project == project)
    if task_category is None:
        q = q.where(ProviderStats.task_category.is_(None))
    else:
        q = q.where(ProviderStats.task_category == task_category)

    result = await session.execute(q)
    rows = result.scalars().all()

    out: dict[str, ProviderStats | None] = {p: None for p in ALL_PROVIDERS}
    for row in rows:
        out[row.provider_name] = row
    return out


async def get_all_provider_stats(
    session: AsyncSession,
    *,
    project: str | None = None,
) -> list[dict]:
    """Return all provider stats rows as dicts for the telemetry API."""
    q = select(ProviderStats)
    if project is not None:
        q = q.where(ProviderStats.project == project)
    q = q.order_by(ProviderStats.provider_name, ProviderStats.task_category)

    result = await session.execute(q)
    rows = result.scalars().all()

    return [
        {
            "provider_name": r.provider_name,
            "project": r.project,
            "task_category": r.task_category,
            "total_sessions": r.total_sessions,
            "useful_sessions": r.useful_sessions,
            "harmful_sessions": r.harmful_sessions,
            "total_memories_contributed": r.total_memories_contributed,
            "usefulness_rate": r.usefulness_rate,
            "harmful_rate": r.harmful_rate,
            "avg_token_efficiency": r.avg_token_efficiency,
            "weight_current": r.weight_current,
            "drift_flagged": r.drift_flagged,
            "drift_reason": r.drift_reason,
            "last_updated_at": r.last_updated_at.isoformat() if r.last_updated_at else None,
        }
        for r in rows
    ]
