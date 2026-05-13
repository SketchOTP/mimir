"""Plan representation: create, validate, and store structured execution plans."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Risk threshold above which approval is required before execution
_HIGH_RISK_THRESHOLD = 0.7
# Keywords that always flag a plan for approval
_HIGH_IMPACT_KEYWORDS = {
    "replace", "rewrite", "delete", "drop", "purge", "irreversible",
    "production", "migrate all", "disable", "shutdown", "remove all",
}


@dataclass
class PlanStep:
    id: str
    description: str
    dependencies: list[str] = field(default_factory=list)   # step IDs that must precede this
    required_procedures: list[str] = field(default_factory=list)  # procedure memory IDs / names
    risk_estimate: float = 0.0
    rollback_option: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "dependencies": self.dependencies,
            "required_procedures": self.required_procedures,
            "risk_estimate": self.risk_estimate,
            "rollback_option": self.rollback_option,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PlanStep":
        return cls(
            id=d["id"],
            description=d["description"],
            dependencies=d.get("dependencies", []),
            required_procedures=d.get("required_procedures", []),
            risk_estimate=d.get("risk_estimate", 0.0),
            rollback_option=d.get("rollback_option"),
        )


def validate_plan_graph(steps: list[PlanStep]) -> dict[str, Any]:
    """Validate plan DAG: check for unknown dependency references and cycles.

    Returns {valid: bool, errors: list[str]}.
    """
    step_ids = {s.id for s in steps}
    errors: list[str] = []

    # Check all dependencies reference known steps
    for step in steps:
        for dep in step.dependencies:
            if dep not in step_ids:
                errors.append(f"Step '{step.id}' depends on unknown step '{dep}'")

    # Cycle detection via DFS
    adj: dict[str, list[str]] = {s.id: s.dependencies for s in steps}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {sid: WHITE for sid in step_ids}

    def dfs(node: str) -> bool:
        if color[node] == GRAY:
            return True  # back edge = cycle
        if color[node] == BLACK:
            return False
        color[node] = GRAY
        for neighbor in adj.get(node, []):
            if neighbor in color and dfs(neighbor):
                return True
        color[node] = BLACK
        return False

    for sid in step_ids:
        if color[sid] == WHITE and dfs(sid):
            errors.append("Plan graph contains a dependency cycle")
            break

    return {"valid": len(errors) == 0, "errors": errors}


def _requires_approval(goal: str, risk_estimate: float) -> bool:
    goal_lower = goal.lower()
    keyword_hit = any(kw in goal_lower for kw in _HIGH_IMPACT_KEYWORDS)
    return risk_estimate >= _HIGH_RISK_THRESHOLD or keyword_hit


async def create_plan(
    session: AsyncSession,
    goal: str,
    steps: list[dict],
    risk_estimate: float = 0.0,
    confidence_estimate: float = 0.5,
    rollback_options: list[str] | None = None,
    expected_outcomes: list[str] | None = None,
    project: str | None = None,
    user_id: str | None = None,
) -> "SimulationPlan":  # noqa: F821
    from storage.models import SimulationPlan

    risk_estimate = max(0.0, min(1.0, risk_estimate))
    confidence_estimate = max(0.1, min(0.95, confidence_estimate))

    plan_steps = [PlanStep.from_dict(s) if isinstance(s, dict) else s for s in steps]
    validation = validate_plan_graph(plan_steps)

    approval_required = _requires_approval(goal, risk_estimate)
    status = "pending_approval" if approval_required else "draft"

    plan = SimulationPlan(
        id=str(uuid.uuid4()),
        goal=goal,
        status=status,
        steps=[s.to_dict() if isinstance(s, PlanStep) else s for s in plan_steps],
        risk_estimate=risk_estimate,
        confidence_estimate=confidence_estimate,
        rollback_options=rollback_options or [],
        expected_outcomes=expected_outcomes or [],
        approval_required=approval_required,
        graph_valid=validation["valid"],
        graph_errors=validation["errors"],
        project=project,
        user_id=user_id,
    )
    session.add(plan)
    await session.flush()
    return plan


async def get_plan(session: AsyncSession, plan_id: str) -> "SimulationPlan | None":  # noqa: F821
    from storage.models import SimulationPlan
    result = await session.execute(select(SimulationPlan).where(SimulationPlan.id == plan_id))
    return result.scalar_one_or_none()


async def list_plans(
    session: AsyncSession,
    project: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list["SimulationPlan"]:  # noqa: F821
    from storage.models import SimulationPlan
    q = select(SimulationPlan).order_by(SimulationPlan.created_at.desc()).limit(limit)
    if project is not None:
        q = q.where(SimulationPlan.project == project)
    if status is not None:
        q = q.where(SimulationPlan.status == status)
    result = await session.execute(q)
    return list(result.scalars().all())


async def approve_plan(session: AsyncSession, plan_id: str) -> bool:
    plan = await get_plan(session, plan_id)
    if plan is None or plan.status not in ("pending_approval", "draft"):
        return False
    plan.status = "approved"
    await session.flush()
    return True


async def reject_plan(session: AsyncSession, plan_id: str, reason: str = "") -> bool:
    plan = await get_plan(session, plan_id)
    if plan is None or plan.status == "executed":
        return False
    plan.status = "rejected"
    if reason:
        plan.meta = {**(plan.meta or {}), "rejection_reason": reason}
    await session.flush()
    return True
