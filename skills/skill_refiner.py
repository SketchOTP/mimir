"""Refine skills based on run history and propose updated versions."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import SkillRun, Skill
from skills import skill_registry


async def propose_refinement(session: AsyncSession, skill_id: str) -> dict | None:
    skill = await skill_registry.get(session, skill_id)
    if not skill:
        return None

    runs_q = select(SkillRun).where(SkillRun.skill_id == skill_id).order_by(SkillRun.created_at.desc()).limit(20)
    result = await session.execute(runs_q)
    runs = list(result.scalars())

    if len(runs) < 5:
        return None

    failures = [r for r in runs if r.outcome != "success"]
    failure_rate = len(failures) / len(runs)

    if failure_rate < 0.2:
        return None  # No refinement needed

    # Collect common errors
    errors = [r.error for r in failures if r.error]
    common_error = max(set(errors), key=errors.count) if errors else "unknown"

    return {
        "skill_id": skill_id,
        "skill_name": skill.name,
        "current_version": skill.version,
        "failure_rate": round(failure_rate, 2),
        "common_error": common_error,
        "suggestion": f"Refine error handling for: {common_error}",
        "recommended_action": "propose_improvement",
    }
