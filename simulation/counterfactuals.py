"""Counterfactual reasoning: 'What if X had been done differently?'

Given a plan and a hypothetical modification, estimate how the outcome
would have differed, using the same historical evidence as the simulator.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, UTC

from sqlalchemy.ext.asyncio import AsyncSession

_CONFIDENCE_FLOOR = 0.10
_CONFIDENCE_CEILING = 0.95


def _clamp(v: float) -> float:
    return max(_CONFIDENCE_FLOOR, min(_CONFIDENCE_CEILING, v))


@dataclass
class CounterfactualResult:
    counterfactual_id: str
    plan_id: str
    scenario: str                           # human-readable description of the "what if"
    original_outcome: "OutcomeEstimate"     # noqa: F821
    counterfactual_outcome: "OutcomeEstimate"  # noqa: F821
    probability_delta: float                # counterfactual.success - original.success
    risk_delta: float                       # counterfactual.risk - original.risk
    confidence: float
    supporting_evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "counterfactual_id": self.counterfactual_id,
            "plan_id": self.plan_id,
            "scenario": self.scenario,
            "original_outcome": self.original_outcome.to_dict(),
            "counterfactual_outcome": self.counterfactual_outcome.to_dict(),
            "probability_delta": round(self.probability_delta, 4),
            "risk_delta": round(self.risk_delta, 4),
            "confidence": self.confidence,
            "supporting_evidence": self.supporting_evidence,
            "would_improve": self.would_improve,
            "verdict": self.verdict,
        }

    @property
    def would_improve(self) -> bool:
        return self.probability_delta > 0.02

    @property
    def verdict(self) -> str:
        if self.probability_delta > 0.10:
            return "strong_improvement"
        if self.probability_delta > 0.02:
            return "marginal_improvement"
        if self.probability_delta < -0.10:
            return "strong_degradation"
        if self.probability_delta < -0.02:
            return "marginal_degradation"
        return "no_significant_change"


async def run_counterfactual(
    session: AsyncSession,
    plan_id: str,
    scenario: str,
    *,
    # Optional modifiers — apply one or more to the plan before re-estimating
    override_risk: float | None = None,
    add_procedures: list[str] | None = None,
    remove_procedures: list[str] | None = None,
    add_rollback_option: str | None = None,
    project: str | None = None,
) -> CounterfactualResult | None:
    """Run a counterfactual analysis.

    Returns None if plan not found.
    Simulates 'what if X had been done differently' by modifying the plan
    parameters and re-running outcome estimation.
    """
    from simulation.planner import get_plan
    from simulation.outcome_estimator import estimate_outcome
    from storage.models import SimulationRun
    import copy

    plan = await get_plan(session, plan_id)
    if plan is None:
        return None

    # Baseline estimate
    original = await estimate_outcome(session, plan, project=project)

    # Build a transient modified plan object (in-memory, not persisted)
    class _PlanProxy:
        pass

    cf_plan = _PlanProxy()
    cf_plan.steps = copy.deepcopy(plan.steps or [])
    cf_plan.risk_estimate = plan.risk_estimate
    cf_plan.project = project or plan.project
    cf_plan.user_id = plan.user_id
    cf_plan.meta = plan.meta

    if override_risk is not None:
        cf_plan.risk_estimate = max(0.0, min(1.0, override_risk))

    # Apply procedure modifications to steps
    if add_procedures or remove_procedures:
        for step in cf_plan.steps:
            procs = step.get("required_procedures", [])
            if add_procedures:
                procs = procs + [p for p in add_procedures if p not in procs]
            if remove_procedures:
                procs = [p for p in procs if p not in remove_procedures]
            step["required_procedures"] = procs

    if add_rollback_option:
        # Reduce risk estimate slightly — presence of rollback is safer
        cf_plan.risk_estimate = max(0.0, cf_plan.risk_estimate - 0.10)

    # Counterfactual estimate
    cf_estimate = await estimate_outcome(session, cf_plan, project=project)

    prob_delta = cf_estimate.success_probability - original.success_probability
    risk_delta = cf_estimate.risk_score - original.risk_score
    confidence = _clamp(
        (original.confidence_score + cf_estimate.confidence_score) / 2
    )

    supporting: list[str] = []
    all_memory_ids = list(set(original.supporting_memories + cf_estimate.supporting_memories))
    if all_memory_ids:
        supporting = all_memory_ids[:5]

    cf_id = str(uuid.uuid4())
    now = datetime.now(UTC).replace(tzinfo=None)

    # Persist as a counterfactual simulation run
    sim_run = SimulationRun(
        id=cf_id,
        plan_id=plan_id,
        simulation_type="counterfactual",
        counterfactual_description=scenario,
        status="complete",
        paths=[],
        best_path_id=None,
        success_probability=cf_estimate.success_probability,
        risk_score=cf_estimate.risk_score,
        confidence_score=confidence,
        expected_failure_modes=cf_estimate.expected_failure_modes,
        historical_memories_used=supporting,
        tokens_used=0,
        depth_reached=0,
        project=project or plan.project,
        user_id=plan.user_id,
        completed_at=now,
    )
    session.add(sim_run)
    await session.flush()

    return CounterfactualResult(
        counterfactual_id=cf_id,
        plan_id=plan_id,
        scenario=scenario,
        original_outcome=original,
        counterfactual_outcome=cf_estimate,
        probability_delta=round(prob_delta, 4),
        risk_delta=round(risk_delta, 4),
        confidence=confidence,
        supporting_evidence=supporting,
    )


async def list_counterfactuals(
    session: AsyncSession,
    plan_id: str,
    limit: int = 20,
) -> list[dict]:
    """Return all counterfactual runs for a plan."""
    from sqlalchemy import select
    from storage.models import SimulationRun

    q = (
        select(SimulationRun)
        .where(
            SimulationRun.plan_id == plan_id,
            SimulationRun.simulation_type == "counterfactual",
        )
        .order_by(SimulationRun.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(q)
    runs = result.scalars().all()
    return [
        {
            "id": r.id,
            "scenario": r.counterfactual_description,
            "success_probability": r.success_probability,
            "risk_score": r.risk_score,
            "confidence_score": r.confidence_score,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in runs
    ]
