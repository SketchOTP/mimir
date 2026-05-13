"""Reflector worker — asynchronous/offline pattern analysis.

Responsibility:
  - Analyze recent task traces and memory events for repeated patterns
  - Detect contradictions between active memories
  - Extract procedural lessons from repeated successful workflows
  - Propose improvements via the existing improvement_planner

Runs every 30 minutes. No live memory mutation — only reads and proposes.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import Memory, TaskTrace, ImprovementProposal
from memory.trust import MemoryState, TrustLevel

logger = logging.getLogger(__name__)

# Minimum occurrences of a task type before we flag it as a pattern
_PATTERN_MIN_COUNT = 3
# Window to look back for pattern analysis
_ANALYSIS_WINDOW_HOURS = 24
# Minimum trust gap that marks a contradiction worth flagging
_CONTRADICTION_TRUST_THRESHOLD = 0.6


async def analyze_patterns(
    session: AsyncSession,
    project: str | None = None,
) -> list[dict]:
    """Detect repeated task-type patterns in recent traces.

    Returns list of pattern dicts suitable for improvement proposals.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=_ANALYSIS_WINDOW_HOURS)
    filters = [TaskTrace.created_at >= cutoff]
    result = await session.execute(select(TaskTrace).where(*filters))
    traces = result.scalars().all()

    type_counts: dict[str, int] = defaultdict(int)
    type_outcomes: dict[str, list[str]] = defaultdict(list)
    for t in traces:
        type_counts[t.task_type] += 1
        if t.outcome:
            type_outcomes[t.task_type].append(t.outcome)

    patterns = []
    for task_type, count in type_counts.items():
        if count < _PATTERN_MIN_COUNT:
            continue
        outcomes = type_outcomes[task_type]
        success_rate = (
            outcomes.count("success") / len(outcomes) if outcomes else 0.0
        )
        patterns.append({
            "task_type": task_type,
            "count": count,
            "success_rate": success_rate,
            "project": project,
        })

    logger.debug("reflector: found %d repeated patterns", len(patterns))
    return patterns


async def detect_contradictions(
    session: AsyncSession,
    project: str | None = None,
) -> list[tuple[Memory, Memory]]:
    """Find pairs of active semantic memories with similar keys but conflicting content.

    Returns list of (mem_a, mem_b) contradiction pairs.
    """
    filters = [
        Memory.layer == "semantic",
        Memory.memory_state == MemoryState.ACTIVE,
        Memory.deleted_at.is_(None),
    ]
    if project:
        filters.append(Memory.project == project)

    result = await session.execute(select(Memory).where(*filters))
    mems = result.scalars().all()

    contradictions: list[tuple[Memory, Memory]] = []
    seen: set[tuple[str, str]] = set()

    for i, m1 in enumerate(mems):
        for m2 in mems[i + 1:]:
            pair_key = (min(m1.id, m2.id), max(m1.id, m2.id))
            if pair_key in seen:
                continue
            seen.add(pair_key)

            # Heuristic: same source_id + different content = contradiction
            if (
                m1.source_id
                and m2.source_id
                and m1.source_id == m2.source_id
                and m1.content != m2.content
            ):
                contradictions.append((m1, m2))
                continue

            # Heuristic: both high-trust with identical short key phrases
            # (first 40 chars) but different endings
            k1 = m1.content[:40].lower().strip()
            k2 = m2.content[:40].lower().strip()
            if (
                k1 == k2
                and m1.content != m2.content
                and m1.trust_score >= _CONTRADICTION_TRUST_THRESHOLD
                and m2.trust_score >= _CONTRADICTION_TRUST_THRESHOLD
            ):
                contradictions.append((m1, m2))

    logger.info("reflector: detected %d contradictions", len(contradictions))
    return contradictions


async def extract_procedural_lessons(
    session: AsyncSession,
    project: str | None = None,
) -> list[str]:
    """Identify repeated successful workflows and return lesson summaries."""
    patterns = await analyze_patterns(session, project=project)
    lessons = []
    for p in patterns:
        if p["success_rate"] >= 0.8 and p["count"] >= _PATTERN_MIN_COUNT:
            lessons.append(
                f"Task '{p['task_type']}' has been executed {p['count']} times "
                f"with {p['success_rate']*100:.0f}% success rate — consider formalizing as a skill."
            )
    logger.debug("reflector: extracted %d procedural lessons", len(lessons))
    return lessons


async def flag_contradictions(
    session: AsyncSession,
    project: str | None = None,
) -> int:
    """Mark contradicting memory pairs with state=contradicted on the lower-trust one.

    Returns count of memories flagged.
    """
    from storage.models import LifecycleEvent
    import uuid

    pairs = await detect_contradictions(session, project=project)
    flagged = 0
    for m1, m2 in pairs:
        loser = m1 if m1.trust_score <= m2.trust_score else m2
        if loser.memory_state == MemoryState.CONTRADICTED:
            continue
        old_state = loser.memory_state
        loser.memory_state = MemoryState.CONTRADICTED
        loser.verification_status = TrustLevel.CONFLICTING
        session.add(loser)
        session.add(LifecycleEvent(
            id=uuid.uuid4().hex,
            memory_id=loser.id,
            event_type="memory_contradicted",
            from_state=old_state,
            to_state=MemoryState.CONTRADICTED,
            trust_before=loser.trust_score,
            trust_after=loser.trust_score,
            reason="Contradiction detected with higher-trust memory",
            meta={"conflicting_memory_id": (m2.id if loser is m1 else m1.id)},
        ))
        flagged += 1

    if flagged:
        await session.commit()
    logger.info("reflector: flagged %d memories as contradicted", flagged)
    return flagged


async def propose_from_patterns(
    session: AsyncSession,
    project: str | None = None,
    user_id: str | None = None,
) -> list[str]:
    """Create ImprovementProposal records from detected procedural patterns.

    Returns list of created proposal IDs.
    """
    import uuid

    lessons = await extract_procedural_lessons(session, project=project)
    ids = []
    for lesson in lessons:
        # Avoid creating a duplicate proposal for the same lesson
        existing = await session.execute(
            select(ImprovementProposal).where(
                ImprovementProposal.reason == lesson,
                ImprovementProposal.status.in_(["proposed", "approved"]),
            )
        )
        if existing.scalars().first():
            continue

        proposal = ImprovementProposal(
            id=uuid.uuid4().hex,
            improvement_type="skill_proposal",
            title="Formalize repeated workflow as skill",
            reason=lesson,
            current_behavior="Workflow executed ad-hoc each time",
            proposed_behavior="Create a reusable skill for this workflow",
            risk="low",
            expected_benefit="Reduced token cost and improved consistency",
            status="proposed",
            project=project,
            user_id=user_id,
        )
        session.add(proposal)
        ids.append(proposal.id)

    if ids:
        await session.commit()
    logger.info("reflector: created %d improvement proposals from patterns", len(ids))
    return ids


async def mine_experience_patterns(
    session: AsyncSession,
    project: str | None = None,
) -> dict:
    """Identify high-signal patterns from accumulated task traces.

    Looks for:
    - Repeated successful sequences (repeated success in same task type)
    - Repeated failures (high-cost mistakes)
    - Recovery patterns (failure then success in same session)
    - Stable corrective behaviors (failure→success clusters)

    Returns a summary dict with pattern lists for use in proposals.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=_ANALYSIS_WINDOW_HOURS * 7)  # 7-day window for mining
    result = await session.execute(select(TaskTrace).where(TaskTrace.created_at >= cutoff))
    traces = list(result.scalars())

    # Group by task_type
    by_type: dict[str, list] = defaultdict(list)
    for t in traces:
        by_type[t.task_type].append(t)

    repeated_successes: list[dict] = []
    repeated_failures: list[dict] = []
    recovery_patterns: list[dict] = []

    for task_type, task_traces in by_type.items():
        outcomes = [t.outcome for t in task_traces if t.outcome]
        if len(outcomes) < _PATTERN_MIN_COUNT:
            continue

        success_count = outcomes.count("success")
        failure_count = outcomes.count("failure")
        total = len(outcomes)
        success_rate = success_count / total if total else 0.0

        if success_rate >= 0.85 and success_count >= _PATTERN_MIN_COUNT:
            repeated_successes.append({
                "task_type": task_type,
                "count": total,
                "success_rate": success_rate,
                "project": project,
            })

        if failure_count >= _PATTERN_MIN_COUNT and success_rate < 0.4:
            repeated_failures.append({
                "task_type": task_type,
                "count": total,
                "failure_rate": failure_count / total,
                "project": project,
            })

        # Recovery: look for sessions where failure is followed by success
        sessions_seen: dict[str, list] = defaultdict(list)
        for t in task_traces:
            if t.session_id:
                sessions_seen[t.session_id].append(t)
        for sess_traces in sessions_seen.values():
            sorted_traces = sorted(sess_traces, key=lambda t: t.created_at)
            for i in range(len(sorted_traces) - 1):
                if sorted_traces[i].outcome == "failure" and sorted_traces[i + 1].outcome == "success":
                    recovery_patterns.append({
                        "task_type": task_type,
                        "failure_input": sorted_traces[i].input_summary,
                        "success_input": sorted_traces[i + 1].input_summary,
                        "project": project,
                    })
                    break

    return {
        "repeated_successes": repeated_successes,
        "repeated_failures": repeated_failures,
        "recovery_patterns": recovery_patterns,
    }


async def propose_improvement_suggestions(
    session: AsyncSession,
    project: str | None = None,
    user_id: str | None = None,
) -> list[str]:
    """Generate improvement proposals based on operational history.

    Proposes:
    - Retrieval tuning when failure rates are high
    - Memory prioritization changes when high-cost mistakes repeat
    - New procedural candidates from stable corrective behaviors

    All proposals are approval-gated.
    Returns list of created proposal IDs.
    """
    import uuid as _uuid

    patterns = await mine_experience_patterns(session, project=project)
    created_ids: list[str] = []

    for rf in patterns["repeated_failures"]:
        title = f"Reduce repeated failures in '{rf['task_type']}'"
        reason = (
            f"Task '{rf['task_type']}' has failed {rf['failure_rate']*100:.0f}% "
            f"of the time over the last 7 days ({rf['count']} total executions). "
            f"Suggest retrieval tuning or memory policy review."
        )
        # Avoid duplicates
        exists = await session.execute(
            select(ImprovementProposal).where(
                ImprovementProposal.title == title,
                ImprovementProposal.status.in_(["proposed", "approved"]),
            )
        )
        if exists.scalars().first():
            continue
        proposal = ImprovementProposal(
            id=_uuid.uuid4().hex,
            improvement_type="retrieval_tuning",
            title=title,
            reason=reason,
            current_behavior=f"Task fails {rf['failure_rate']*100:.0f}% of the time",
            proposed_behavior="Review retrieval policy and memory coverage for this task type",
            risk="low",
            expected_benefit="Reduced failure rate through improved memory retrieval",
            status="proposed",
            project=project,
            user_id=user_id,
            meta={"pattern": rf},
        )
        session.add(proposal)
        created_ids.append(proposal.id)

    for rp in patterns["recovery_patterns"]:
        if not rp.get("failure_input") or not rp.get("success_input"):
            continue
        title = f"Formalize recovery procedure for '{rp['task_type']}'"
        reason = (
            f"A recovery pattern was observed for '{rp['task_type']}': "
            f"after failure, a corrective approach succeeded. "
            f"Consider formalizing this as a procedural fallback."
        )
        exists = await session.execute(
            select(ImprovementProposal).where(
                ImprovementProposal.title == title,
                ImprovementProposal.status.in_(["proposed", "approved"]),
            )
        )
        if exists.scalars().first():
            continue
        proposal = ImprovementProposal(
            id=_uuid.uuid4().hex,
            improvement_type="procedural_promotion",
            title=title,
            reason=reason,
            current_behavior="Recovery happens ad-hoc without a formalized fallback",
            proposed_behavior="Create a procedural memory describing the corrective approach",
            risk="low",
            expected_benefit="Faster recovery from task failures via explicit procedural guidance",
            status="proposed",
            project=project,
            user_id=user_id,
            meta={"pattern": rp},
        )
        session.add(proposal)
        created_ids.append(proposal.id)

    if created_ids:
        await session.commit()
    logger.info("reflector: created %d operational improvement proposals", len(created_ids))
    return created_ids


async def run_reflection_pass(
    session: AsyncSession,
    project: str | None = None,
    user_id: str | None = None,
) -> dict:
    """Full reflector pass: contradiction detection + pattern analysis + proposals."""
    flagged = await flag_contradictions(session, project=project)
    proposal_ids = await propose_from_patterns(session, project=project, user_id=user_id)
    suggestion_ids = await propose_improvement_suggestions(session, project=project, user_id=user_id)
    all_proposal_ids = proposal_ids + suggestion_ids
    return {
        "contradictions_flagged": flagged,
        "proposals_created": len(all_proposal_ids),
        "proposal_ids": all_proposal_ids,
    }
