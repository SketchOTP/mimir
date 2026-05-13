"""Execute a skill and record the result."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import Skill, SkillRun
from skills import skill_registry

logger = logging.getLogger(__name__)


async def run(
    session: AsyncSession,
    skill_id: str,
    input_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    skill = await skill_registry.get(session, skill_id)
    if not skill:
        return {"ok": False, "error": "skill_not_found"}
    if skill.status not in ("active", "draft"):
        return {"ok": False, "error": f"skill_status_{skill.status}"}

    run_id = f"run_{uuid.uuid4().hex[:16]}"
    start = time.monotonic()

    try:
        output = await _execute_steps(skill, input_data or {})
        elapsed = int((time.monotonic() - start) * 1000)
        outcome = "success"
        error = None
    except Exception as e:
        output = {}
        elapsed = int((time.monotonic() - start) * 1000)
        outcome = "error"
        error = str(e)
        logger.exception("Skill %s run failed", skill_id)

    run_record = SkillRun(
        id=run_id,
        skill_id=skill_id,
        skill_version=skill.version,
        input_data=input_data,
        output_data=output,
        outcome=outcome,
        duration_ms=elapsed,
        error=error,
    )
    session.add(run_record)
    await skill_registry.record_run_outcome(session, skill_id, outcome == "success")
    await session.commit()

    return {"ok": outcome == "success", "run_id": run_id, "output": output, "error": error}


async def record_result(
    session: AsyncSession,
    skill_id: str,
    run_id: str,
    outcome: str,
    output_data: dict | None = None,
) -> bool:
    run_rec = await session.get(SkillRun, run_id)
    if not run_rec:
        return False
    run_rec.outcome = outcome
    if output_data:
        run_rec.output_data = output_data
    await skill_registry.record_run_outcome(session, skill_id, outcome == "success")
    await session.commit()
    return True


async def _execute_steps(skill: Skill, input_data: dict) -> dict:
    """
    Stub executor: in production, route step actions to registered tool handlers.
    Returns execution trace.
    """
    results = []
    for step in skill.steps:
        results.append(
            {
                "step": step.get("order"),
                "action": step.get("action"),
                "status": "executed",
                "note": "stub_execution",
            }
        )
    return {"steps_executed": len(results), "results": results, "input": input_data}
