"""Estimate likely outcomes for a plan using historical procedural data.

Uses:
- procedural memory success/failure rates
- rollback history
- trust scores
- causal chain telemetry
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Confidence floor/ceiling — never claim extremes
_CONFIDENCE_FLOOR = 0.10
_CONFIDENCE_CEILING = 0.95

# Minimum trust to count a procedural memory as supporting evidence
_MIN_PROCEDURE_TRUST = 0.50


@dataclass
class OutcomeEstimate:
    success_probability: float          # [0.10, 0.95]
    risk_score: float                   # [0, 1]
    confidence_score: float             # [0.10, 0.95]
    expected_failure_modes: list[str] = field(default_factory=list)
    supporting_memories: list[str] = field(default_factory=list)  # memory IDs
    evidence_count: int = 0

    def to_dict(self) -> dict:
        return {
            "success_probability": self.success_probability,
            "risk_score": self.risk_score,
            "confidence_score": self.confidence_score,
            "expected_failure_modes": self.expected_failure_modes,
            "supporting_memories": self.supporting_memories,
            "evidence_count": self.evidence_count,
        }


def _clamp(v: float) -> float:
    return max(_CONFIDENCE_FLOOR, min(_CONFIDENCE_CEILING, v))


async def _gather_procedural_evidence(
    session: AsyncSession,
    procedure_names: list[str],
    project: str | None,
) -> tuple[list[str], float, float, list[str]]:
    """Return (memory_ids, success_rate, failure_rate, failure_modes)."""
    from storage.models import Memory

    if not procedure_names:
        return [], 0.5, 0.0, []

    q = (
        select(Memory)
        .where(
            Memory.layer == "procedural",
            Memory.deleted_at.is_(None),
            Memory.trust_score >= _MIN_PROCEDURE_TRUST,
        )
        .limit(20)
    )
    if project:
        q = q.where(Memory.project == project)

    result = await session.execute(q)
    memories = list(result.scalars().all())

    # Filter to those whose content mentions any procedure name
    matched: list = []
    proc_lower = [p.lower() for p in procedure_names]
    for m in memories:
        content_lower = m.content.lower()
        if any(pn in content_lower for pn in proc_lower) or not proc_lower:
            matched.append(m)

    if not matched:
        return [], 0.5, 0.0, []

    memory_ids = [m.id for m in matched]
    total_retrievals = sum(
        (m.successful_retrievals or 0) + (m.failed_retrievals or 0) for m in matched
    )
    total_success = sum(m.successful_retrievals or 0 for m in matched)
    total_failure = sum(m.failed_retrievals or 0 for m in matched)

    if total_retrievals == 0:
        # No retrieval history — use trust as proxy
        avg_trust = sum(m.trust_score for m in matched) / len(matched)
        success_rate = avg_trust
        failure_rate = 1.0 - avg_trust
    else:
        success_rate = total_success / total_retrievals
        failure_rate = total_failure / total_retrievals

    # Collect failure modes from memories that have failed
    failure_modes: list[str] = []
    for m in matched:
        if (m.failed_retrievals or 0) > 0 and m.summary:
            failure_modes.append(m.summary[:120])

    return memory_ids, success_rate, failure_rate, failure_modes[:5]


async def _count_rollbacks_for_procedures(
    session: AsyncSession,
    procedure_names: list[str],
    project: str | None,
) -> int:
    """Count rollbacks in the past 90 days that are related to given procedures."""
    from storage.models import Rollback
    from datetime import datetime, timedelta, UTC

    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=90)
    q = select(Rollback).where(Rollback.created_at >= cutoff).limit(100)
    result = await session.execute(q)
    rollbacks = list(result.scalars().all())

    if not procedure_names:
        return len(rollbacks)

    proc_lower = [p.lower() for p in procedure_names]
    count = 0
    for r in rollbacks:
        reason_lower = (r.reason or "").lower()
        if any(pn in reason_lower for pn in proc_lower):
            count += 1

    return count


async def estimate_outcome(
    session: AsyncSession,
    plan,  # SimulationPlan ORM object
    project: str | None = None,
) -> OutcomeEstimate:
    """Estimate outcome for a plan using historical evidence."""
    steps = plan.steps or []
    all_procedures: list[str] = []
    for step in steps:
        all_procedures.extend(step.get("required_procedures", []))

    effective_project = project or plan.project

    memory_ids, success_rate, failure_rate, failure_modes = await _gather_procedural_evidence(
        session, all_procedures, effective_project
    )

    rollback_count = await _count_rollbacks_for_procedures(
        session, all_procedures, effective_project
    )

    # Composite success probability
    rollback_penalty = min(0.30, rollback_count * 0.05)
    risk_from_steps = (plan.risk_estimate or 0.0)

    raw_success = success_rate * (1.0 - rollback_penalty) * (1.0 - risk_from_steps * 0.3)
    success_probability = _clamp(raw_success)

    risk_score = _clamp(1.0 - success_probability + rollback_penalty * 0.5)

    # Confidence: higher when we have more evidence
    evidence_count = len(memory_ids)
    raw_confidence = 0.10 + min(0.85, evidence_count * 0.12)
    confidence_score = _clamp(raw_confidence)

    if rollback_count > 0:
        failure_modes.insert(0, f"{rollback_count} related rollback(s) in last 90 days")

    return OutcomeEstimate(
        success_probability=success_probability,
        risk_score=risk_score,
        confidence_score=confidence_score,
        expected_failure_modes=failure_modes,
        supporting_memories=memory_ids,
        evidence_count=evidence_count,
    )
