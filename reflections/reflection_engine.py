"""Generate reflections by analyzing recent events, outcomes, and metrics."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, UTC
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import Reflection, Memory, SkillRun, TaskTrace, MetricRecord, RetrievalLog

logger = logging.getLogger(__name__)

# Triggers that warrant a reflection — normal success is NOT in this list
VALID_REFLECTION_TRIGGERS = frozenset([
    "user_correction",
    "task_failure",
    "repeated_inefficiency",
    "repeated_success",
    "rollback_event",
    "approval_rejection",
    "retrieval_miss",
    "memory_conflict",
    "scheduled",   # scheduled is allowed but gated by should_reflect()
    "manual",
])


def should_reflect(trigger: str, context: dict | None = None) -> bool:
    """
    Return True only when there is a meaningful reason to create a reflection.

    A normal successful task does not qualify.  Scheduled reflections only
    qualify when the context contains at least one signal worth acting on.
    """
    if trigger not in VALID_REFLECTION_TRIGGERS:
        return False

    # Event-driven triggers always qualify
    if trigger in ("user_correction", "task_failure", "rollback_event",
                   "approval_rejection", "retrieval_miss", "memory_conflict",
                   "manual"):
        return True

    # repeated_* qualify only when a meaningful repeat count is provided
    if trigger in ("repeated_inefficiency", "repeated_success"):
        count = (context or {}).get("repeat_count", 0)
        return int(count) >= 3

    # Scheduled: only when the context signals something actionable
    if trigger == "scheduled":
        ctx = context or {}
        return bool(
            ctx.get("has_failures")
            or ctx.get("has_retrieval_miss")
            or ctx.get("has_low_metric")
        )

    return False


async def log_reflection(
    session: AsyncSession,
    trigger: str,
    observations: list[str],
    lessons: list[str],
    proposed_improvements: list[dict] | None = None,
    project: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
) -> Reflection:
    ref = Reflection(
        id=f"ref_{uuid.uuid4().hex[:16]}",
        project=project,
        trigger=trigger,
        observations=observations,
        lessons=lessons,
        proposed_improvements=proposed_improvements or [],
        session_id=session_id,
        user_id=user_id,
    )
    session.add(ref)
    await session.commit()
    return ref


async def generate(
    session: AsyncSession,
    project: str | None = None,
    window_hours: int = 24,
) -> Reflection | None:
    """
    Auto-generate a scheduled reflection from recent system activity.

    Returns None (and skips persisting) when there is nothing actionable —
    i.e. the system is operating normally with no failures, misses, or low metrics.
    """
    since = datetime.now(UTC) - timedelta(hours=window_hours)

    observations = []
    lessons = []
    proposed = []
    has_failures = False
    has_retrieval_miss = False
    has_low_metric = False

    # Skill run failures
    skill_runs_q = await session.execute(
        select(SkillRun).where(SkillRun.created_at > since).limit(100)
    )
    runs = list(skill_runs_q.scalars())
    if runs:
        failures = [r for r in runs if r.outcome != "success"]
        fail_rate = len(failures) / len(runs)
        observations.append(f"Skill run failure rate: {fail_rate:.0%} ({len(failures)}/{len(runs)})")
        if fail_rate > 0.3:
            has_failures = True
            lessons.append("High skill failure rate — review failing skills and consider refinement")
            proposed.append(
                {
                    "type": "skill_refine",
                    "reason": f"Failure rate {fail_rate:.0%} exceeds 30% threshold",
                    "priority": "high",
                }
            )

    # Task trace failures
    task_q = await session.execute(
        select(TaskTrace).where(TaskTrace.created_at > since).limit(200)
    )
    tasks = list(task_q.scalars())
    if tasks:
        failed_tasks = [t for t in tasks if t.outcome == "failure"]
        if failed_tasks:
            has_failures = True
            task_types = {t.task_type for t in failed_tasks}
            observations.append(f"Task failures in: {', '.join(task_types)}")
            lessons.append(f"Repeated failures in {', '.join(task_types)} — may need procedural memories")

    # Retrieval miss detection: count RetrievalLog entries that returned 0 results
    ret_zero_q = await session.execute(
        select(func.count()).select_from(RetrievalLog).where(
            RetrievalLog.results_count == 0,
            RetrievalLog.created_at > since,
        )
    )
    zero_results = ret_zero_q.scalar_one()
    if zero_results > 0:
        has_retrieval_miss = True
        observations.append(f"{zero_results} retrieval queries returned zero results")
        lessons.append("Retrieval misses detected — memory coverage may be insufficient")
        proposed.append({"type": "retrieval_tune", "reason": f"{zero_results} empty results", "priority": "medium"})

    # Low metric check
    metrics_q = await session.execute(
        select(MetricRecord)
        .where(MetricRecord.recorded_at > since)
        .order_by(MetricRecord.recorded_at.desc())
        .limit(50)
    )
    metrics = list(metrics_q.scalars())
    for m in metrics:
        if m.name == "retrieval_relevance_score" and m.value < 0.5:
            has_low_metric = True
            observations.append(f"Low retrieval relevance: {m.value:.2f}")
            lessons.append("Retrieval quality below threshold — consider adjusting embedding strategy")
            proposed.append({"type": "retrieval_tune", "reason": "Low relevance score", "priority": "medium"})
            break

    # Gate: only create a reflection if there is something actionable
    gate_context = {
        "has_failures": has_failures,
        "has_retrieval_miss": has_retrieval_miss,
        "has_low_metric": has_low_metric,
    }
    if not should_reflect("scheduled", gate_context):
        logger.debug("Scheduled reflection skipped — no actionable signals in last %dh", window_hours)
        return None

    if not observations:
        observations.append(f"No significant events in the last {window_hours}h")
    if not lessons:
        lessons.append("System operating normally — no immediate actions needed")

    return await log_reflection(
        session,
        trigger="scheduled",
        observations=observations,
        lessons=lessons,
        proposed_improvements=proposed,
        project=project,
    )


async def list_reflections(
    session: AsyncSession,
    project: str | None = None,
    limit: int = 20,
) -> list[Reflection]:
    q = select(Reflection).order_by(Reflection.created_at.desc()).limit(limit)
    if project:
        q = q.where(Reflection.project == project)
    result = await session.execute(q)
    return list(result.scalars())
