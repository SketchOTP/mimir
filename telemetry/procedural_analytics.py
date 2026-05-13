"""Procedural effectiveness analytics + confidence drift detection.

Tracks procedural memory success/failure rates over time and detects
memories that are becoming unreliable (confidence drift).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import Memory, RetrievalFeedback, LifecycleEvent
from memory.trust import MemoryState

logger = logging.getLogger(__name__)

# Thresholds for drift detection
_DRIFT_MIN_RETRIEVALS = 3          # minimum retrievals before drift is detectable
_DRIFT_FAILURE_RATE_THRESHOLD = 0.5  # >50% failure rate signals drift
_DRIFT_TRUST_DECAY_THRESHOLD = 0.15  # trust dropped >0.15 from initial suggests drift
_DRIFT_LOOKBACK_DAYS = 14


async def get_procedural_effectiveness(
    session: AsyncSession,
    memory_id: str,
) -> dict | None:
    """Return effectiveness metrics for a single procedural memory."""
    mem = await session.get(Memory, memory_id)
    if not mem or mem.deleted_at or mem.layer != "procedural":
        return None

    total = (mem.successful_retrievals or 0) + (mem.failed_retrievals or 0)
    success_rate = round(
        (mem.successful_retrievals or 0) / total, 4
    ) if total > 0 else None
    failure_rate = round(
        (mem.failed_retrievals or 0) / total, 4
    ) if total > 0 else None

    # Count rollback events linked to this memory via lifecycle events
    rb_result = await session.execute(
        select(LifecycleEvent).where(
            LifecycleEvent.memory_id == memory_id,
            LifecycleEvent.event_type == "trust_decreased",
        )
    )
    trust_decrease_events = rb_result.scalars().all()
    rollback_count = sum(
        1 for e in trust_decrease_events
        if e.reason and "harmful" in e.reason
    )

    # Supersession count
    supersession_result = await session.execute(
        select(LifecycleEvent).where(
            LifecycleEvent.memory_id == memory_id,
            LifecycleEvent.event_type == "memory_superseded",
        )
    )
    supersession_count = len(supersession_result.scalars().all())

    return {
        "memory_id": memory_id,
        "layer": mem.layer,
        "content_snippet": mem.content[:120] if mem.content else "",
        "trust_score": mem.trust_score,
        "confidence": mem.confidence,
        "evidence_count": mem.evidence_count or 0,
        "times_retrieved": mem.times_retrieved or 0,
        "successful_retrievals": mem.successful_retrievals or 0,
        "failed_retrievals": mem.failed_retrievals or 0,
        "success_rate": success_rate,
        "failure_rate": failure_rate,
        "rollback_count": rollback_count,
        "supersession_count": supersession_count,
        "last_success_at": mem.last_success_at.isoformat() if mem.last_success_at else None,
        "last_failure_at": mem.last_failure_at.isoformat() if mem.last_failure_at else None,
        "memory_state": mem.memory_state,
        "avg_outcome_quality": round(
            (mem.successful_retrievals or 0) * 1.0 / max(1, mem.times_retrieved or 1), 4
        ),
        "evidence_growth_rate": round(
            (mem.evidence_count or 0) / max(1, mem.times_retrieved or 1), 4
        ),
    }


async def get_all_procedural_effectiveness(
    session: AsyncSession,
    *,
    project: str | None = None,
    min_retrievals: int = 1,
) -> list[dict]:
    """Return effectiveness for all procedural memories with at least min_retrievals."""
    filters = [
        Memory.layer == "procedural",
        Memory.deleted_at.is_(None),
        Memory.times_retrieved >= min_retrievals,
    ]
    if project:
        filters.append(Memory.project == project)

    result = await session.execute(
        select(Memory).where(*filters).order_by(Memory.trust_score.desc())
    )
    mems = result.scalars().all()

    out = []
    for mem in mems:
        eff = await get_procedural_effectiveness(session, mem.id)
        if eff:
            out.append(eff)
    return out


async def detect_confidence_drift(
    session: AsyncSession,
    *,
    project: str | None = None,
) -> list[dict]:
    """Find memories showing signs of confidence drift.

    Drift indicators:
    - High-trust memory repeatedly causing failures (failure_rate > 50%)
    - Trust has decayed significantly from a high starting point
    - Procedural memory with many failed retrievals in the lookback window

    Returns list of drift candidates with recommended action.
    """
    cutoff = datetime.now(UTC) - timedelta(days=_DRIFT_LOOKBACK_DAYS)

    filters = [
        Memory.deleted_at.is_(None),
        Memory.memory_state.notin_(list(MemoryState.BLOCKED)),
        Memory.times_retrieved >= _DRIFT_MIN_RETRIEVALS,
    ]
    if project:
        filters.append(Memory.project == project)

    result = await session.execute(select(Memory).where(*filters))
    mems = result.scalars().all()

    drift_candidates = []
    for mem in mems:
        total = (mem.successful_retrievals or 0) + (mem.failed_retrievals or 0)
        if total < _DRIFT_MIN_RETRIEVALS:
            continue

        failure_rate = (mem.failed_retrievals or 0) / total
        if failure_rate < _DRIFT_FAILURE_RATE_THRESHOLD:
            continue

        # Check recent feedback pattern (last N days)
        fb_result = await session.execute(
            select(RetrievalFeedback).where(
                RetrievalFeedback.memory_id == mem.id,
                RetrievalFeedback.created_at >= cutoff,
            )
        )
        recent_feedbacks = fb_result.scalars().all()
        recent_failures = sum(
            1 for f in recent_feedbacks if f.outcome in ("failure", "harmful", "irrelevant")
        )
        recent_total = len(recent_feedbacks)

        # Count trust-decrease lifecycle events
        le_result = await session.execute(
            select(LifecycleEvent).where(
                LifecycleEvent.memory_id == mem.id,
                LifecycleEvent.event_type == "trust_decreased",
                LifecycleEvent.created_at >= cutoff,
            )
        )
        trust_decreases = len(le_result.scalars().all())

        # Infer recommended action based on severity
        is_high_trust = (mem.trust_score or 0) >= 0.7
        recent_failure_rate = (
            recent_failures / recent_total if recent_total > 0 else failure_rate
        )

        if recent_failure_rate >= 0.7 and is_high_trust:
            action = "review_and_decay"
        elif mem.layer == "procedural" and failure_rate >= 0.6:
            action = "age_and_propose_supersession"
        else:
            action = "accelerate_aging"

        drift_candidates.append({
            "memory_id": mem.id,
            "layer": mem.layer,
            "content_snippet": mem.content[:100] if mem.content else "",
            "trust_score": mem.trust_score,
            "memory_state": mem.memory_state,
            "times_retrieved": mem.times_retrieved or 0,
            "failure_rate": round(failure_rate, 4),
            "recent_failure_rate": round(recent_failure_rate, 4),
            "recent_trust_decreases": trust_decreases,
            "recommended_action": action,
        })

    # Sort by severity (recent failure rate desc)
    drift_candidates.sort(key=lambda d: d["recent_failure_rate"], reverse=True)
    return drift_candidates


async def apply_drift_trust_decay(
    session: AsyncSession,
    drift_candidates: list[dict],
) -> int:
    """Apply conservative trust decay to confirmed drift candidates.

    Safety constraints:
    - Never auto-quarantine — only decay trust
    - Max decay per pass: -0.05
    - Never decay below 0.01
    - Never touch quarantined memories
    """
    _MAX_DECAY = 0.05
    _TRUST_FLOOR = 0.01
    decayed = 0

    for candidate in drift_candidates:
        if candidate["recommended_action"] not in ("review_and_decay", "accelerate_aging"):
            continue

        mem = await session.get(Memory, candidate["memory_id"])
        if not mem or mem.deleted_at:
            continue
        if mem.memory_state in MemoryState.BLOCKED:
            continue

        old_trust = mem.trust_score or 0.7
        decay = min(_MAX_DECAY, old_trust - _TRUST_FLOOR)
        if decay <= 0:
            continue

        mem.trust_score = round(max(_TRUST_FLOOR, old_trust - decay), 4)
        session.add(mem)
        decayed += 1

    if decayed:
        await session.commit()
    logger.info("procedural_analytics: drift decay applied to %d memories", decayed)
    return decayed
