"""Retrieval analytics — quality scoring, heatmaps, provider usefulness.

Computes per-session quality scores and aggregates for the telemetry dashboard.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, UTC

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import Memory, RetrievalSession, RetrievalFeedback

logger = logging.getLogger(__name__)


def compute_session_quality_scores(
    retrieved_memory_ids: list[str],
    token_cost: int,
    token_budget: int,
    agreement_scores: dict[str, float],
    *,
    task_outcome: str | None = None,
    has_harmful_outcome: bool = False,
) -> dict[str, float]:
    """Compute retrieval quality scores for a single orchestrated retrieval.

    Returns: relevance, usefulness, harmfulness, agreement, token_efficiency
    All scores are in [0.0, 1.0].
    """
    if not retrieved_memory_ids:
        return {
            "relevance_score": 0.0,
            "usefulness_score": 0.0,
            "harmfulness_score": 0.0,
            "agreement_score": 0.0,
            "token_efficiency_score": 0.0,
        }

    # Agreement score: mean provider agreement across retrieved memories
    if agreement_scores:
        covered = [agreement_scores.get(mid, 0.0) for mid in retrieved_memory_ids]
        agreement = sum(covered) / len(covered) if covered else 0.0
    else:
        agreement = 0.0

    # Relevance: proxy via agreement (higher cross-provider agreement = higher relevance)
    relevance = min(1.0, agreement * 1.2)  # slight uplift — agreement is conservative

    # Usefulness: depends on task outcome if known
    if task_outcome == "success":
        usefulness = min(1.0, 0.6 + agreement * 0.4)
    elif task_outcome in ("failure", "partial"):
        usefulness = max(0.0, 0.3 - agreement * 0.2)
    else:
        usefulness = agreement * 0.7  # unknown outcome — estimate from agreement

    # Harmfulness
    harmfulness = 1.0 if has_harmful_outcome else 0.0

    # Token efficiency: how well the budget was used
    if token_budget > 0 and token_cost > 0:
        # 1.0 when cost == budget; penalise both over and under use
        ratio = token_cost / token_budget
        token_efficiency = 1.0 - abs(1.0 - min(ratio, 2.0))
        token_efficiency = max(0.0, token_efficiency)
    else:
        token_efficiency = 0.5  # unknown

    return {
        "relevance_score": round(relevance, 4),
        "usefulness_score": round(usefulness, 4),
        "harmfulness_score": round(harmfulness, 4),
        "agreement_score": round(agreement, 4),
        "token_efficiency_score": round(token_efficiency, 4),
    }


async def get_memory_heatmap(
    session: AsyncSession,
    *,
    project: str | None = None,
    limit: int = 20,
    window_days: int = 30,
) -> dict:
    """Return retrieval usage heatmap — most/least used memories.

    Returns:
      most_used, most_successful, most_harmful, rarely_used, high_cost_low_value
    """
    cutoff = datetime.now(UTC) - timedelta(days=window_days)
    filters = [
        Memory.deleted_at.is_(None),
        Memory.times_retrieved > 0,
    ]
    if project:
        filters.append(Memory.project == project)

    result = await session.execute(
        select(Memory)
        .where(*filters)
        .order_by(Memory.times_retrieved.desc())
        .limit(limit * 3)
    )
    mems = result.scalars().all()

    def _summary(m: Memory) -> dict:
        total = (m.successful_retrievals or 0) + (m.failed_retrievals or 0)
        return {
            "id": m.id,
            "layer": m.layer,
            "content_snippet": m.content[:100] if m.content else "",
            "trust_score": m.trust_score,
            "times_retrieved": m.times_retrieved or 0,
            "successful_retrievals": m.successful_retrievals or 0,
            "failed_retrievals": m.failed_retrievals or 0,
            "success_rate": round(
                (m.successful_retrievals or 0) / total, 3
            ) if total > 0 else None,
            "importance": m.importance,
            "memory_state": m.memory_state,
        }

    most_used = sorted(mems, key=lambda m: m.times_retrieved or 0, reverse=True)[:limit]
    most_successful = sorted(
        mems, key=lambda m: m.successful_retrievals or 0, reverse=True
    )[:limit]
    rarely_used = sorted(mems, key=lambda m: m.times_retrieved or 0)[:limit]

    # High cost, low value: high retrieval frequency but low success rate
    with_rate = [
        m for m in mems
        if (m.successful_retrievals or 0) + (m.failed_retrievals or 0) >= 2
    ]
    high_cost_low_value = sorted(
        with_rate,
        key=lambda m: (-(m.times_retrieved or 0),
                       (m.successful_retrievals or 0) / max(1, (m.successful_retrievals or 0) + (m.failed_retrievals or 0))),
    )[:limit]

    return {
        "most_used": [_summary(m) for m in most_used],
        "most_successful": [_summary(m) for m in most_successful],
        "rarely_used": [_summary(m) for m in rarely_used],
        "high_cost_low_value": [_summary(m) for m in high_cost_low_value],
        "window_days": window_days,
        "total_tracked": len(mems),
    }


async def get_retrieval_session_stats(
    session: AsyncSession,
    *,
    project: str | None = None,
    window_hours: int = 24,
) -> dict:
    """Aggregate stats over recent retrieval sessions."""
    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
    filters = [RetrievalSession.created_at >= cutoff]
    if project:
        filters.append(RetrievalSession.project == project)

    result = await session.execute(select(RetrievalSession).where(*filters))
    sessions = result.scalars().all()

    if not sessions:
        return {"total_sessions": 0, "window_hours": window_hours}

    outcomes = [s.task_outcome for s in sessions if s.task_outcome]
    outcome_counts = {}
    for o in outcomes:
        outcome_counts[o] = outcome_counts.get(o, 0) + 1

    avg_token_cost = sum(s.token_cost or 0 for s in sessions) / len(sessions)
    avg_result_count = sum(s.result_count or 0 for s in sessions) / len(sessions)

    usefulness_values = [s.usefulness_score for s in sessions if s.usefulness_score is not None]
    avg_usefulness = sum(usefulness_values) / len(usefulness_values) if usefulness_values else None

    return {
        "total_sessions": len(sessions),
        "sessions_with_outcome": len(outcomes),
        "outcome_distribution": outcome_counts,
        "avg_token_cost": round(avg_token_cost, 1),
        "avg_result_count": round(avg_result_count, 1),
        "avg_usefulness_score": round(avg_usefulness, 4) if avg_usefulness is not None else None,
        "sessions_with_rollback": sum(1 for s in sessions if s.rollback_id),
        "sessions_with_correction": sum(1 for s in sessions if s.has_correction),
        "sessions_with_harmful": sum(1 for s in sessions if s.has_harmful_outcome),
        "window_hours": window_hours,
    }
