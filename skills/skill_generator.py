"""Detect repeated task patterns and propose new skills."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import TaskTrace, Skill
from skills import skill_registry

logger = logging.getLogger(__name__)

# Minimum total observations before proposing a skill
MIN_REPEAT_COUNT = 3
# Minimum fraction of traces that must be successful
MIN_SUCCESS_RATE = 0.5
# Minimum confidence score to emit a skill proposal
MIN_CONFIDENCE = 0.6


def _compute_confidence(total: int, successful: int, steps_count: int) -> float:
    """Score confidence 0.0–1.0 based on observation volume and step clarity."""
    if total == 0:
        return 0.0
    rate = successful / total
    # Volume bonus: more observations → higher confidence
    volume_score = min(1.0, total / 10.0)
    # Step clarity: no steps at all is low confidence
    step_score = min(1.0, steps_count / 3.0)
    return round(rate * 0.6 + volume_score * 0.3 + step_score * 0.1, 3)


def _generate_test_cases(task_type: str, traces: list[TaskTrace]) -> list[dict[str, Any]]:
    """Generate minimal test cases from observed successful traces."""
    cases = []
    for trace in traces[:3]:
        cases.append({
            "description": f"Observed success for {task_type}",
            "input": trace.input_summary or "",
            "expected_outcome": "success",
            "source_trace_id": trace.id,
        })
    return cases


async def analyze_and_propose(session: AsyncSession, project: str | None = None) -> list[Skill]:
    """Scan task traces for repeated patterns and auto-propose skills."""
    q = select(TaskTrace).order_by(TaskTrace.created_at.desc()).limit(500)
    result = await session.execute(q)
    traces = list(result.scalars())

    # Group by task_type
    grouped: dict[str, list[TaskTrace]] = defaultdict(list)
    for trace in traces:
        grouped[trace.task_type].append(trace)

    proposed = []
    for task_type, task_traces in grouped.items():
        if len(task_traces) < MIN_REPEAT_COUNT:
            continue

        # Check if skill already exists for this task type
        existing = await session.execute(
            select(Skill).where(
                Skill.meta["source_task_type"].astext == task_type,
                Skill.status.notin_(["deprecated", "rolled_back"]),
            )
        )
        if existing.scalars().first():
            continue

        # Gate: minimum success rate
        successful = [t for t in task_traces if t.outcome == "success"]
        if not successful:
            continue
        success_rate = len(successful) / len(task_traces)
        if success_rate < MIN_SUCCESS_RATE:
            logger.debug(
                "Skipping skill proposal for %s: success rate %.0f%% below threshold",
                task_type, success_rate * 100,
            )
            continue

        tools_used = set()
        for t in successful:
            for tool in (t.tools_used or []):
                tools_used.add(tool)

        steps = _derive_steps(successful)

        # Gate: must have a derivable trigger condition (always true here via task_type_match)
        trigger_conditions = [{"type": "task_type_match", "value": task_type}]

        # Gate: must be able to generate at least one test case
        test_cases = _generate_test_cases(task_type, successful)
        if not test_cases:
            logger.debug("Skipping skill proposal for %s: no test cases derivable", task_type)
            continue

        # Gate: confidence must meet threshold
        confidence = _compute_confidence(len(task_traces), len(successful), len(steps))
        if confidence < MIN_CONFIDENCE:
            logger.debug(
                "Skipping skill proposal for %s: confidence %.2f below threshold", task_type, confidence
            )
            continue

        skill = await skill_registry.create(
            session,
            name=f"Auto: {task_type.replace('_', ' ').title()}",
            purpose=f"Automate {task_type} based on {len(successful)} observed successes",
            trigger_conditions=trigger_conditions,
            steps=steps,
            tools_required=list(tools_used),
            test_cases=test_cases,
            project=project,
            source_task_ids=[t.id for t in task_traces[:10]],
            meta={
                "source_task_type": task_type,
                "observed_count": len(task_traces),
                "success_rate": round(success_rate, 3),
                "confidence": confidence,
                "rollback_target": None,  # set after first promotion
            },
        )
        logger.info(
            "Proposed skill %s for %s (confidence=%.2f, success_rate=%.0f%%)",
            skill.id, task_type, confidence, success_rate * 100,
        )
        proposed.append(skill)

    return proposed


def _derive_steps(traces: list[TaskTrace]) -> list[dict[str, Any]]:
    """Extract common steps from successful task traces."""
    all_steps: list[dict] = []
    for trace in traces[:5]:
        if trace.tools_used:
            for i, tool in enumerate(trace.tools_used):
                all_steps.append({"order": i + 1, "action": f"use_tool:{tool}", "tool": tool})

    # Deduplicate by action
    seen = set()
    unique = []
    for step in all_steps:
        if step["action"] not in seen:
            seen.add(step["action"])
            unique.append(step)
    return unique
