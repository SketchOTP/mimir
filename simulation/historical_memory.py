"""Store simulation results as retrievable semantic memories.

Called after a simulation run completes so future planning tasks can
retrieve evidence from historical simulations via the normal memory pipeline.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import Memory


async def store_simulation_memory(
    session: AsyncSession,
    plan,
    run,
) -> str:
    """Create a semantic Memory row summarising a completed simulation.

    Returns the memory id.  Idempotent: if a memory already exists for this
    run_id (stored in meta['simulation_run_id']) it is returned unchanged.
    """
    # Idempotency check
    existing = (await session.execute(
        select(Memory).where(
            Memory.source_type == "simulation",
            Memory.source_id == run.id,
        )
    )).scalars().first()
    if existing:
        return existing.id

    paths = run.paths or []
    best = next((p for p in paths if p.get("path_id") == run.best_path_id), None)
    best_summary = ""
    if best:
        best_summary = (
            f" Best path '{best.get('path_name', 'base')}' with "
            f"success_prob={best.get('success_probability', run.success_probability):.2f}, "
            f"risk={best.get('risk_score', run.risk_score):.2f}."
        )

    failure_modes = run.expected_failure_modes or []
    failure_str = f" Failure modes: {'; '.join(failure_modes[:3])}." if failure_modes else ""

    sim_type = run.simulation_type or "simulation"
    content = (
        f"[{sim_type.upper()} EVIDENCE] Goal: {plan.goal}. "
        f"Predicted success={run.success_probability:.2f}, "
        f"risk={run.risk_score:.2f}, "
        f"confidence={run.confidence_score:.2f}."
        f"{best_summary}"
        f"{failure_str}"
        f" Paths explored: {len(paths)}. "
        f"Plan status: {plan.status}. "
        f"Project: {plan.project or 'unset'}."
    )

    mem_id = str(uuid.uuid4())
    mem = Memory(
        id=mem_id,
        layer="semantic",
        content=content,
        summary=f"Simulation: {plan.goal[:80]}",
        project=plan.project,
        importance=min(0.9, 0.5 + run.risk_score * 0.4),
        trust_score=run.confidence_score,
        confidence=run.confidence_score,
        source_type="simulation",
        source_id=run.id,
        verification_status="trusted_system_observed",
        memory_state="active",
        meta={
            "plan_id": plan.id,
            "simulation_run_id": run.id,
            "simulation_type": sim_type,
            "success_probability": run.success_probability,
            "risk_score": run.risk_score,
            "best_path_id": run.best_path_id,
            "plan_goal": plan.goal,
        },
    )
    session.add(mem)
    return mem_id


async def get_simulation_context(
    session: AsyncSession,
    goal_keywords: list[str],
    *,
    project: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """Return recent simulation memories whose content overlaps with keywords.

    Used by simulation_provider to build retrieval context.
    """
    q = select(Memory).where(
        Memory.source_type == "simulation",
        Memory.deleted_at.is_(None),
        Memory.memory_state == "active",
    )
    if project:
        q = q.where(Memory.project == project)
    q = q.order_by(Memory.created_at.desc()).limit(limit * 5)

    mems = (await session.execute(q)).scalars().all()
    if not mems:
        return []

    keywords = [k.lower() for k in goal_keywords if len(k) > 2]
    if not keywords:
        return [{"id": m.id, "score": 0.5} for m in mems[:limit]]

    scored: list[tuple[Memory, float]] = []
    for mem in mems:
        lower = mem.content.lower()
        matches = sum(1 for k in keywords if k in lower)
        if matches > 0:
            scored.append((mem, matches / len(keywords)))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [{"id": m.id, "score": score} for m, score in scored[:limit]]
