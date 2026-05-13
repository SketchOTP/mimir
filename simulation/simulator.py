"""Core simulation engine: multi-path simulation with safety bounds."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, UTC

from sqlalchemy.ext.asyncio import AsyncSession

# Safety constraints
MAX_DEPTH = 5
MAX_BRANCHES = 3
SIMULATION_TOKEN_BUDGET = 10_000
_TOKENS_PER_STEP = 200  # rough estimate per simulated step

_CONFIDENCE_FLOOR = 0.10
_CONFIDENCE_CEILING = 0.95


@dataclass
class SimulationPath:
    path_id: str
    description: str
    steps: list[str]                    # ordered step IDs
    success_probability: float
    risk_score: float
    token_cost_estimate: int
    historical_effectiveness: float     # [0, 1] from procedural evidence
    rollback_risk: float                # [0, 1]

    def to_dict(self) -> dict:
        return {
            "path_id": self.path_id,
            "description": self.description,
            "steps": self.steps,
            "success_probability": self.success_probability,
            "risk_score": self.risk_score,
            "token_cost_estimate": self.token_cost_estimate,
            "historical_effectiveness": self.historical_effectiveness,
            "rollback_risk": self.rollback_risk,
        }


@dataclass
class SimulationResult:
    simulation_id: str
    plan_id: str
    paths: list[SimulationPath] = field(default_factory=list)
    best_path_id: str | None = None
    overall_success_probability: float = 0.5
    overall_risk_score: float = 0.5
    overall_confidence: float = 0.10
    expected_failure_modes: list[str] = field(default_factory=list)
    recommendation: str = ""
    tokens_used: int = 0
    depth_reached: int = 0
    branches_explored: int = 0
    bounded_by: str = "none"   # none|depth|branches|token_budget

    def to_dict(self) -> dict:
        return {
            "id": self.simulation_id,
            "simulation_id": self.simulation_id,
            "plan_id": self.plan_id,
            "paths": [p.to_dict() for p in self.paths],
            "best_path_id": self.best_path_id,
            "overall_success_probability": self.overall_success_probability,
            "overall_risk_score": self.overall_risk_score,
            "overall_confidence": self.overall_confidence,
            "expected_failure_modes": self.expected_failure_modes,
            "recommendation": self.recommendation,
            "tokens_used": self.tokens_used,
            "depth_reached": self.depth_reached,
            "branches_explored": self.branches_explored,
            "bounded_by": self.bounded_by,
        }


def _clamp(v: float) -> float:
    return max(_CONFIDENCE_FLOOR, min(_CONFIDENCE_CEILING, v))


def _topological_order(steps: list[dict]) -> list[str]:
    """Return step IDs in dependency-safe execution order (Kahn's algorithm)."""
    id_to_step = {s["id"]: s for s in steps}
    in_degree = {s["id"]: 0 for s in steps}
    for s in steps:
        for dep in s.get("dependencies", []):
            if dep in in_degree:
                in_degree[s["id"]] += 1

    queue = [sid for sid, deg in in_degree.items() if deg == 0]
    order: list[str] = []
    while queue:
        node = queue.pop(0)
        order.append(node)
        for s in steps:
            if node in s.get("dependencies", []):
                in_degree[s["id"]] -= 1
                if in_degree[s["id"]] == 0:
                    queue.append(s["id"])
    # Append any remaining (cycle-breaker safety)
    for sid in id_to_step:
        if sid not in order:
            order.append(sid)
    return order


def _generate_path_variants(
    base_order: list[str],
    steps: list[dict],
    estimate: "OutcomeEstimate",  # noqa: F821
    max_branches: int,
) -> list[SimulationPath]:
    """Generate up to max_branches path variants from a base plan."""
    paths: list[SimulationPath] = []
    id_to_step = {s["id"]: s for s in steps}

    # Path 0: base order (fastest)
    base_token_cost = len(base_order) * _TOKENS_PER_STEP
    base_path = SimulationPath(
        path_id=f"path_base_{str(uuid.uuid4())[:8]}",
        description="Base execution order (fastest path, higher risk)",
        steps=base_order,
        success_probability=_clamp(estimate.success_probability),
        risk_score=_clamp(estimate.risk_score),
        token_cost_estimate=base_token_cost,
        historical_effectiveness=_clamp(estimate.success_probability),
        rollback_risk=_clamp(estimate.risk_score * 0.8),
    )
    paths.append(base_path)

    if max_branches < 2:
        return paths

    # Path 1: validation-first (add validation prefix step, lower risk)
    val_order = ["_validate"] + base_order
    val_risk = _clamp(estimate.risk_score * 0.6)
    val_success = _clamp(estimate.success_probability * 1.15)
    val_path = SimulationPath(
        path_id=f"path_validated_{str(uuid.uuid4())[:8]}",
        description="Validation-first path (slower, lower risk)",
        steps=val_order,
        success_probability=val_success,
        risk_score=val_risk,
        token_cost_estimate=base_token_cost + _TOKENS_PER_STEP,
        historical_effectiveness=_clamp(val_success),
        rollback_risk=_clamp(val_risk * 0.5),
    )
    paths.append(val_path)

    if max_branches < 3:
        return paths

    # Path 2: rollback-safe (explicit rollback checkpoints between high-risk steps)
    high_risk_steps = [
        s["id"] for s in steps if s.get("risk_estimate", 0.0) > 0.5
    ]
    if high_risk_steps:
        safe_order = []
        for sid in base_order:
            safe_order.append(sid)
            if sid in high_risk_steps:
                safe_order.append(f"_checkpoint_{sid}")
        safe_token_cost = len(safe_order) * _TOKENS_PER_STEP
        safe_risk = _clamp(estimate.risk_score * 0.4)
        safe_success = _clamp(estimate.success_probability * 1.05)
        safe_path = SimulationPath(
            path_id=f"path_safe_{str(uuid.uuid4())[:8]}",
            description="Rollback-safe path with checkpoints after each high-risk step",
            steps=safe_order,
            success_probability=safe_success,
            risk_score=safe_risk,
            token_cost_estimate=safe_token_cost,
            historical_effectiveness=_clamp(safe_success),
            rollback_risk=_clamp(safe_risk * 0.3),
        )
        paths.append(safe_path)
    else:
        # No high-risk steps — add a staged rollout variant
        staged = base_order[:len(base_order) // 2 + 1]
        staged_path = SimulationPath(
            path_id=f"path_staged_{str(uuid.uuid4())[:8]}",
            description="Staged rollout (first half of steps, lower token cost)",
            steps=staged,
            success_probability=_clamp(estimate.success_probability * 1.05),
            risk_score=_clamp(estimate.risk_score * 0.5),
            token_cost_estimate=len(staged) * _TOKENS_PER_STEP,
            historical_effectiveness=_clamp(estimate.success_probability),
            rollback_risk=_clamp(estimate.risk_score * 0.4),
        )
        paths.append(staged_path)

    return paths


def _best_path(paths: list[SimulationPath]) -> SimulationPath | None:
    if not paths:
        return None
    # Score: success_probability * 0.6 - risk_score * 0.4
    return max(paths, key=lambda p: p.success_probability * 0.6 - p.risk_score * 0.4)


async def run_simulation(
    session: AsyncSession,
    plan_id: str,
    max_depth: int = MAX_DEPTH,
    max_branches: int = MAX_BRANCHES,
    token_budget: int = SIMULATION_TOKEN_BUDGET,
    project: str | None = None,
) -> SimulationResult | None:
    """Run a bounded multi-path simulation for a plan.

    Returns None if the plan is not found.
    """
    from simulation.planner import get_plan
    from simulation.outcome_estimator import estimate_outcome
    from storage.models import SimulationRun

    # Enforce bounds
    max_depth = min(max_depth, MAX_DEPTH)
    max_branches = min(max_branches, MAX_BRANCHES)
    token_budget = min(token_budget, SIMULATION_TOKEN_BUDGET)

    plan = await get_plan(session, plan_id)
    if plan is None:
        return None

    steps = plan.steps or []
    estimate = await estimate_outcome(session, plan, project=project)

    base_order = _topological_order(steps)
    depth_reached = min(len(base_order), max_depth)

    tokens_consumed = 0
    bounded_by = "none"

    # Respect token budget
    if len(base_order) * _TOKENS_PER_STEP > token_budget:
        bounded_by = "token_budget"
        max_steps = token_budget // _TOKENS_PER_STEP
        base_order = base_order[:max_steps]
        depth_reached = max_steps

    tokens_consumed = len(base_order) * _TOKENS_PER_STEP

    paths = _generate_path_variants(base_order, steps, estimate, max_branches)
    branches_explored = len(paths)

    if branches_explored >= max_branches and bounded_by == "none":
        bounded_by = "branches"
    if depth_reached >= max_depth and bounded_by == "none":
        bounded_by = "depth"

    best = _best_path(paths)
    overall_success = _clamp(
        sum(p.success_probability for p in paths) / len(paths) if paths else 0.5
    )
    overall_risk = _clamp(
        sum(p.risk_score for p in paths) / len(paths) if paths else 0.5
    )

    recommendation = _build_recommendation(best, overall_risk)

    sim_id = str(uuid.uuid4())
    now = datetime.now(UTC).replace(tzinfo=None)

    sim_run = SimulationRun(
        id=sim_id,
        plan_id=plan_id,
        simulation_type="full",
        status="complete",
        paths=[p.to_dict() for p in paths],
        best_path_id=best.path_id if best else None,
        success_probability=overall_success,
        risk_score=overall_risk,
        confidence_score=estimate.confidence_score,
        expected_failure_modes=estimate.expected_failure_modes,
        historical_memories_used=estimate.supporting_memories,
        tokens_used=tokens_consumed,
        depth_reached=depth_reached,
        project=project or plan.project,
        user_id=plan.user_id,
        completed_at=now,
    )
    session.add(sim_run)
    await session.flush()

    return SimulationResult(
        simulation_id=sim_id,
        plan_id=plan_id,
        paths=paths,
        best_path_id=best.path_id if best else None,
        overall_success_probability=overall_success,
        overall_risk_score=overall_risk,
        overall_confidence=estimate.confidence_score,
        expected_failure_modes=estimate.expected_failure_modes,
        recommendation=recommendation,
        tokens_used=tokens_consumed,
        depth_reached=depth_reached,
        branches_explored=branches_explored,
        bounded_by=bounded_by,
    )


def _build_recommendation(best: SimulationPath | None, overall_risk: float) -> str:
    if best is None:
        return "No paths could be simulated."
    if overall_risk > 0.7:
        return (
            f"HIGH RISK: Use '{best.description}' path. "
            f"Success probability: {best.success_probability:.0%}. "
            "Ensure rollback plan is in place before executing."
        )
    if overall_risk > 0.4:
        return (
            f"MODERATE RISK: Recommended path is '{best.description}'. "
            f"Success probability: {best.success_probability:.0%}."
        )
    return (
        f"LOW RISK: Proceed with '{best.description}'. "
        f"Success probability: {best.success_probability:.0%}."
    )
