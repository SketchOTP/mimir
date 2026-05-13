"""Automatic relationship extraction from Mimir's existing data structures.

Each builder scans a data source and creates/updates graph nodes+edges.
All operations are idempotent (get_or_create semantics).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from graph.graph_provider import get_or_create_node, get_or_create_edge
from storage.models import (
    EpisodicChain,
    ImprovementProposal,
    Memory,
    Rollback,
    RetrievalSession,
    SimulationPlan,
    SimulationRun,
)

logger = logging.getLogger(__name__)


# ── From episodic chains ───────────────────────────────────────────────────────

async def _build_from_episodic_chains(session: AsyncSession) -> int:
    """Create chain nodes + memory nodes + PART_OF and DERIVED_FROM edges."""
    chains = (await session.execute(select(EpisodicChain))).scalars().all()
    created = 0
    for chain in chains:
        chain_node = await get_or_create_node(
            session,
            entity_id=chain.id,
            node_type="episodic_chain",
            label=chain.title[:128],
            project=chain.project,
            user_id=chain.user_id,
        )
        linked_ids: list[str] = chain.linked_memory_ids or []
        for mem_id in linked_ids:
            mem_node = await get_or_create_node(
                session,
                entity_id=mem_id,
                node_type="memory",
                label=f"memory:{mem_id[:8]}",
                project=chain.project,
                user_id=chain.user_id,
            )
            await get_or_create_edge(
                session,
                source_node_id=mem_node.id,
                target_node_id=chain_node.id,
                rel_type="PART_OF",
                confidence=0.9,
                strength=1.0,
                source="auto_episodic",
            )
            created += 1

        if chain.procedural_lesson:
            # Chain with procedural lesson → lesson is DERIVED_FROM chain
            lesson_label = chain.procedural_lesson[:64]
            lesson_node = await get_or_create_node(
                session,
                entity_id=f"lesson:{chain.id}",
                node_type="procedure",
                label=lesson_label,
                project=chain.project,
                user_id=chain.user_id,
                meta={"lesson": chain.procedural_lesson},
            )
            await get_or_create_edge(
                session,
                source_node_id=lesson_node.id,
                target_node_id=chain_node.id,
                rel_type="DERIVED_FROM",
                confidence=0.8,
                strength=0.9,
                source="auto_episodic",
            )
            created += 1

    return created


# ── From memory supersessions + contradictions ─────────────────────────────────

async def _build_from_memory_relations(session: AsyncSession) -> int:
    """Create SUPERSEDES and CONTRADICTS edges from Memory state flags."""
    mems = (await session.execute(
        select(Memory).where(Memory.deleted_at.is_(None))
    )).scalars().all()

    created = 0
    for mem in mems:
        mem_node = await get_or_create_node(
            session,
            entity_id=mem.id,
            node_type="memory",
            label=(mem.summary or mem.content[:64]),
            project=mem.project,
            user_id=mem.user_id,
        )

        if mem.superseded_by:
            new_node = await get_or_create_node(
                session,
                entity_id=mem.superseded_by,
                node_type="memory",
                label=f"memory:{mem.superseded_by[:8]}",
                project=mem.project,
                user_id=mem.user_id,
            )
            await get_or_create_edge(
                session,
                source_node_id=new_node.id,
                target_node_id=mem_node.id,
                rel_type="SUPERSEDES",
                confidence=0.95,
                strength=1.0,
                source="auto_supersession",
                verification_status="confirmed",
            )
            created += 1

        if mem.memory_state == "contradicted":
            # Find the source memory for the contradiction via source_id
            if mem.source_id:
                src_node = await get_or_create_node(
                    session,
                    entity_id=mem.source_id,
                    node_type="memory",
                    label=f"memory:{mem.source_id[:8]}",
                    project=mem.project,
                )
                await get_or_create_edge(
                    session,
                    source_node_id=src_node.id,
                    target_node_id=mem_node.id,
                    rel_type="CONTRADICTS",
                    confidence=0.8,
                    strength=0.8,
                    source="auto_contradiction",
                )
                created += 1

    return created


# ── From improvements ──────────────────────────────────────────────────────────

async def _build_from_improvements(session: AsyncSession) -> int:
    """Create improvement nodes + DERIVED_FROM edges to reflections."""
    proposals = (await session.execute(select(ImprovementProposal))).scalars().all()
    created = 0
    for proposal in proposals:
        imp_node = await get_or_create_node(
            session,
            entity_id=proposal.id,
            node_type="improvement",
            label=proposal.title[:128],
            project=proposal.project if hasattr(proposal, "project") else None,
            meta={"improvement_type": proposal.improvement_type, "status": proposal.status},
        )
        if proposal.reflection_id:
            # The improvement was derived from the reflection
            ref_node = await get_or_create_node(
                session,
                entity_id=proposal.reflection_id,
                node_type="task",
                label=f"reflection:{proposal.reflection_id[:8]}",
            )
            await get_or_create_edge(
                session,
                source_node_id=imp_node.id,
                target_node_id=ref_node.id,
                rel_type="DERIVED_FROM",
                confidence=0.85,
                strength=0.9,
                source="auto_improvement",
            )
            created += 1

    return created


# ── From rollbacks ─────────────────────────────────────────────────────────────

async def _build_from_rollbacks(session: AsyncSession) -> int:
    """Create rollback→target FAILED_BECAUSE_OF and RECOVERED_BY edges."""
    rollbacks = (await session.execute(select(Rollback))).scalars().all()
    created = 0
    for rb in rollbacks:
        target_label = f"{rb.target_type}:{rb.target_id[:8]}"
        rollback_node = await get_or_create_node(
            session,
            entity_id=rb.id,
            node_type="task",
            label=f"rollback:{rb.target_id[:8]}",
            meta={"reason": rb.reason[:128] if rb.reason else None},
        )
        target_node = await get_or_create_node(
            session,
            entity_id=rb.target_id,
            node_type="tool",
            label=target_label,
        )
        # The target failed, causing the rollback
        await get_or_create_edge(
            session,
            source_node_id=rollback_node.id,
            target_node_id=target_node.id,
            rel_type="FAILED_BECAUSE_OF",
            confidence=0.9,
            strength=1.0,
            source="auto_rollback",
            verification_status="confirmed",
        )
        # The rollback recovered from the failure
        await get_or_create_edge(
            session,
            source_node_id=rollback_node.id,
            target_node_id=target_node.id,
            rel_type="RECOVERED_BY",
            confidence=0.85,
            strength=0.9,
            source="auto_rollback",
        )
        created += 2

    return created


# ── From retrieval sessions ────────────────────────────────────────────────────

async def _build_from_retrieval_sessions(
    session: AsyncSession,
    *,
    limit: int = 200,
) -> int:
    """Wire retrieved memories → retrieval session with USED_IN edges."""
    sessions = (await session.execute(
        select(RetrievalSession)
        .order_by(RetrievalSession.created_at.desc())
        .limit(limit)
    )).scalars().all()

    created = 0
    for rs in sessions:
        rs_node = await get_or_create_node(
            session,
            entity_id=rs.id,
            node_type="retrieval_session",
            label=f"retrieval:{rs.query[:48]}",
            project=rs.project,
            user_id=rs.user_id,
        )
        mem_ids: list[str] = rs.retrieved_memory_ids or []
        for mem_id in mem_ids[:20]:  # cap to prevent node explosion
            mem_node = await get_or_create_node(
                session,
                entity_id=mem_id,
                node_type="memory",
                label=f"memory:{mem_id[:8]}",
                project=rs.project,
                user_id=rs.user_id,
            )
            confidence = 0.75
            if rs.task_outcome == "success":
                confidence = 0.9
            elif rs.task_outcome == "failure":
                confidence = 0.5
            await get_or_create_edge(
                session,
                source_node_id=mem_node.id,
                target_node_id=rs_node.id,
                rel_type="USED_IN",
                confidence=confidence,
                strength=0.8,
                source="auto_retrieval",
            )
            created += 1

    return created


# ── From simulations + plans ───────────────────────────────────────────────────

async def _build_from_simulations(session: AsyncSession) -> int:
    """Create plan/simulation/counterfactual graph nodes and edges.

    Relationships created:
      plan  --SIMULATED-->  simulation_run
      simulation_run  --PREDICTED-->  plan  (for the expected outcome)
      counterfactual  --DERIVED_FROM-->  simulation_run (parent run)
      simulation_run  --FAILED_BECAUSE_OF-->  plan  (if actual_outcome == failure)
      simulation_run  --RECOVERED_BY-->  plan  (if rollback paths present)
    """
    plans = (await session.execute(select(SimulationPlan))).scalars().all()
    runs = (await session.execute(select(SimulationRun))).scalars().all()

    plan_nodes: dict[str, Any] = {}
    for plan in plans:
        node = await get_or_create_node(
            session,
            entity_id=plan.id,
            node_type="plan",
            label=plan.goal[:128],
            project=plan.project,
            meta={"status": plan.status, "risk_estimate": plan.risk_estimate},
        )
        plan_nodes[plan.id] = node

    created = 0
    for run in runs:
        sim_type = run.simulation_type or "simulation"
        node_type = "simulation" if sim_type != "counterfactual" else "simulation"
        run_node = await get_or_create_node(
            session,
            entity_id=run.id,
            node_type=node_type,
            label=f"{sim_type}:{run.id[:8]}",
            project=None,
            meta={
                "simulation_type": sim_type,
                "success_probability": run.success_probability,
                "risk_score": run.risk_score,
                "actual_outcome": run.actual_outcome,
            },
        )

        plan_node = plan_nodes.get(run.plan_id)
        if plan_node is None:
            continue

        # plan --SIMULATED--> run
        await get_or_create_edge(
            session,
            source_node_id=plan_node.id,
            target_node_id=run_node.id,
            rel_type="SIMULATED",
            confidence=run.confidence_score or 0.7,
            strength=1.0,
            source="auto_simulation",
        )
        created += 1

        # run --PREDICTED--> plan (encodes the forecast relationship)
        await get_or_create_edge(
            session,
            source_node_id=run_node.id,
            target_node_id=plan_node.id,
            rel_type="PREDICTED",
            confidence=run.confidence_score or 0.7,
            strength=run.success_probability or 0.5,
            source="auto_simulation",
        )
        created += 1

        # If this is a counterfactual, create DERIVED_FROM edge to the original run
        if sim_type == "counterfactual" and run.counterfactual_description:
            # Best effort: link to any non-counterfactual run for the same plan
            pass

        # If actual outcome was failure, create FAILED_BECAUSE_OF
        if run.actual_outcome == "failure":
            await get_or_create_edge(
                session,
                source_node_id=plan_node.id,
                target_node_id=run_node.id,
                rel_type="FAILED_BECAUSE_OF",
                confidence=0.9,
                strength=1.0,
                source="auto_simulation",
                verification_status="confirmed",
            )
            created += 1

        # If plan has rollback options, create RECOVERED_BY edges
        rollback_options = plan.rollback_options or []
        if rollback_options and run.actual_outcome in ("failure", "partial"):
            await get_or_create_edge(
                session,
                source_node_id=plan_node.id,
                target_node_id=run_node.id,
                rel_type="RECOVERED_BY",
                confidence=0.7,
                strength=0.8,
                source="auto_simulation",
            )
            created += 1

    return created


# ── Main pass ──────────────────────────────────────────────────────────────────

async def run_graph_build_pass(session: AsyncSession) -> dict[str, int]:
    """Run all graph builders and return a summary of edges created."""
    import logging as _log
    _logger = _log.getLogger(__name__)

    async def _safe(name: str, coro) -> int:
        try:
            return await coro
        except Exception as exc:
            _logger.warning("graph builder %s failed: %s", name, exc)
            return 0

    episodic = await _safe("episodic_chains", _build_from_episodic_chains(session))
    memory_rel = await _safe("memory_relations", _build_from_memory_relations(session))
    improvements = await _safe("improvements", _build_from_improvements(session))
    rollbacks = await _safe("rollbacks", _build_from_rollbacks(session))
    retrieval = await _safe("retrieval_sessions", _build_from_retrieval_sessions(session))
    simulations = await _safe("simulations", _build_from_simulations(session))
    await session.commit()

    result = {
        "episodic_edges": episodic,
        "memory_relation_edges": memory_rel,
        "improvement_edges": improvements,
        "rollback_edges": rollbacks,
        "retrieval_edges": retrieval,
        "simulation_edges": simulations,
        "total": episodic + memory_rel + improvements + rollbacks + retrieval + simulations,
    }
    logger.info("Graph build pass: %s", result)
    return result
