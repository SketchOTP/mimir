"""P12 Simulation Engine API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_session, get_current_user

router = APIRouter(prefix="/simulation", tags=["simulation"])


# ─── Request / Response schemas ───────────────────────────────────────────────

class PlanStepIn(BaseModel):
    id: str
    description: str
    dependencies: list[str] = Field(default_factory=list)
    required_procedures: list[str] = Field(default_factory=list)
    risk_estimate: float = Field(default=0.0, ge=0.0, le=1.0)
    rollback_option: str | None = None


class CreatePlanIn(BaseModel):
    goal: str
    steps: list[PlanStepIn] = Field(default_factory=list)
    risk_estimate: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_estimate: float = Field(default=0.5, ge=0.1, le=0.95)
    rollback_options: list[str] = Field(default_factory=list)
    expected_outcomes: list[str] = Field(default_factory=list)
    project: str | None = None


class RunSimulationIn(BaseModel):
    max_depth: int = Field(default=5, ge=1, le=5)
    max_branches: int = Field(default=3, ge=1, le=3)
    token_budget: int = Field(default=10000, ge=100, le=10000)
    project: str | None = None


class CounterfactualIn(BaseModel):
    scenario: str
    override_risk: float | None = Field(default=None, ge=0.0, le=1.0)
    add_procedures: list[str] = Field(default_factory=list)
    remove_procedures: list[str] = Field(default_factory=list)
    add_rollback_option: str | None = None
    project: str | None = None


class RecordOutcomeIn(BaseModel):
    actual_outcome: str  # success | failure | partial | cancelled


class RejectPlanIn(BaseModel):
    reason: str = ""


# ─── Plan endpoints ───────────────────────────────────────────────────────────

@router.post("/plans")
async def create_plan(
    body: CreatePlanIn,
    session: AsyncSession = Depends(get_session),
    user=Depends(get_current_user),
) -> dict:
    from simulation.planner import create_plan as _create_plan

    plan = await _create_plan(
        session,
        goal=body.goal,
        steps=[s.model_dump() for s in body.steps],
        risk_estimate=body.risk_estimate,
        confidence_estimate=body.confidence_estimate,
        rollback_options=body.rollback_options,
        expected_outcomes=body.expected_outcomes,
        project=body.project,
        user_id=user.id if user else None,
    )
    await session.commit()
    return _plan_to_dict(plan)


@router.get("/plans")
async def list_plans(
    project: str | None = None,
    status: str | None = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
    _user=Depends(get_current_user),
) -> list[dict]:
    from simulation.planner import list_plans as _list_plans
    plans = await _list_plans(session, project=project, status=status, limit=limit)
    return [_plan_to_dict(p) for p in plans]


@router.get("/plans/{plan_id}")
async def get_plan(
    plan_id: str,
    session: AsyncSession = Depends(get_session),
    _user=Depends(get_current_user),
) -> dict:
    from simulation.planner import get_plan as _get_plan
    plan = await _get_plan(session, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return _plan_to_dict(plan)


@router.post("/plans/{plan_id}/approve")
async def approve_plan(
    plan_id: str,
    session: AsyncSession = Depends(get_session),
    _user=Depends(get_current_user),
) -> dict:
    from simulation.planner import approve_plan as _approve_plan
    ok = await _approve_plan(session, plan_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Plan not found or not approvable")
    await session.commit()
    return {"status": "approved", "plan_id": plan_id}


@router.post("/plans/{plan_id}/reject")
async def reject_plan(
    plan_id: str,
    body: RejectPlanIn,
    session: AsyncSession = Depends(get_session),
    _user=Depends(get_current_user),
) -> dict:
    from simulation.planner import reject_plan as _reject_plan
    ok = await _reject_plan(session, plan_id, reason=body.reason)
    if not ok:
        raise HTTPException(status_code=404, detail="Plan not found or already executed")
    await session.commit()
    return {"status": "rejected", "plan_id": plan_id}


# ─── Simulation endpoints ─────────────────────────────────────────────────────

@router.post("/plans/{plan_id}/simulate")
async def run_simulation(
    plan_id: str,
    body: RunSimulationIn,
    session: AsyncSession = Depends(get_session),
    _user=Depends(get_current_user),
) -> dict:
    from simulation.simulator import run_simulation as _run_simulation
    from simulation.planner import get_plan as _get_plan
    from simulation.historical_memory import store_simulation_memory

    result = await _run_simulation(
        session,
        plan_id=plan_id,
        max_depth=body.max_depth,
        max_branches=body.max_branches,
        token_budget=body.token_budget,
        project=body.project,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    await session.commit()

    # Store simulation as retrievable memory evidence (best-effort)
    try:
        from sqlalchemy import select as _select
        from storage.models import SimulationRun as _SimRun
        plan = await _get_plan(session, plan_id)
        run_row = (await session.execute(
            _select(_SimRun).where(_SimRun.id == result.simulation_id)
        )).scalars().first()
        if plan and run_row:
            await store_simulation_memory(session, plan, run_row)
            await session.commit()
    except Exception:
        pass

    return result.to_dict()


@router.get("/plans/{plan_id}/simulations")
async def list_simulations(
    plan_id: str,
    session: AsyncSession = Depends(get_session),
    _user=Depends(get_current_user),
) -> list[dict]:
    from sqlalchemy import select
    from storage.models import SimulationRun

    q = (
        select(SimulationRun)
        .where(SimulationRun.plan_id == plan_id)
        .order_by(SimulationRun.created_at.desc())
        .limit(20)
    )
    result = await session.execute(q)
    runs = result.scalars().all()
    return [_run_to_dict(r) for r in runs]


@router.post("/runs/{run_id}/outcome")
@router.post("/simulations/{run_id}/outcome")
async def record_outcome(
    run_id: str,
    body: RecordOutcomeIn,
    session: AsyncSession = Depends(get_session),
    _user=Depends(get_current_user),
) -> dict:
    from simulation.calibration import record_actual_outcome
    ok = await record_actual_outcome(session, run_id, body.actual_outcome)
    if not ok:
        raise HTTPException(status_code=404, detail="Simulation run not found")
    await session.commit()
    return {"status": "recorded", "run_id": run_id, "actual_outcome": body.actual_outcome}


# ─── Counterfactual endpoints ─────────────────────────────────────────────────

@router.post("/plans/{plan_id}/counterfactual")
async def run_counterfactual(
    plan_id: str,
    body: CounterfactualIn,
    session: AsyncSession = Depends(get_session),
    _user=Depends(get_current_user),
) -> dict:
    from simulation.counterfactuals import run_counterfactual as _run_cf
    result = await _run_cf(
        session,
        plan_id=plan_id,
        scenario=body.scenario,
        override_risk=body.override_risk,
        add_procedures=body.add_procedures or None,
        remove_procedures=body.remove_procedures or None,
        add_rollback_option=body.add_rollback_option,
        project=body.project,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    await session.commit()
    return result.to_dict()


@router.get("/plans/{plan_id}/counterfactuals")
async def list_counterfactuals(
    plan_id: str,
    limit: int = 20,
    session: AsyncSession = Depends(get_session),
    _user=Depends(get_current_user),
) -> list[dict]:
    from simulation.counterfactuals import list_counterfactuals as _list_cf
    return await _list_cf(session, plan_id=plan_id, limit=limit)


# ─── Risk forecasting ─────────────────────────────────────────────────────────

@router.post("/plans/{plan_id}/risk")
async def estimate_risk(
    plan_id: str,
    project: str | None = None,
    session: AsyncSession = Depends(get_session),
    _user=Depends(get_current_user),
) -> dict:
    """Quick risk-only estimate without full multi-path simulation."""
    from simulation.planner import get_plan as _get_plan
    from simulation.outcome_estimator import estimate_outcome

    plan = await _get_plan(session, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")

    estimate = await estimate_outcome(session, plan, project=project)
    return {
        "plan_id": plan_id,
        "risk_score": estimate.risk_score,
        "success_probability": estimate.success_probability,
        "risk_forecast": {
            "success_probability": estimate.success_probability,
            "risk_score": estimate.risk_score,
            "confidence_score": estimate.confidence_score,
            "expected_failure_modes": estimate.expected_failure_modes,
            "evidence_count": estimate.evidence_count,
        },
        "high_risk": estimate.risk_score > 0.7,
        "recommendation": (
            "Requires approval or rollback plan before execution"
            if estimate.risk_score > 0.7
            else "Proceed with standard caution"
        ),
    }


# ─── Forecast calibration endpoints ──────────────────────────────────────────

@router.post("/calibration/compute")
async def compute_calibration(
    project: str | None = None,
    period: str = "daily",
    lookback_days: int = 30,
    session: AsyncSession = Depends(get_session),
    _user=Depends(get_current_user),
) -> dict:
    from simulation.calibration import compute_calibration as _compute
    result = await _compute(session, project=project, period=period, lookback_days=lookback_days)
    await session.commit()
    return result


@router.get("/calibration/history")
async def get_calibration_history(
    project: str | None = None,
    limit: int = 30,
    session: AsyncSession = Depends(get_session),
    _user=Depends(get_current_user),
) -> list[dict]:
    from simulation.calibration import get_calibration_history as _get_history
    return await _get_history(session, project=project, limit=limit)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _plan_to_dict(plan) -> dict:
    return {
        "id": plan.id,
        "goal": plan.goal,
        "status": plan.status,
        "steps": plan.steps or [],
        "risk_estimate": plan.risk_estimate,
        "confidence_estimate": plan.confidence_estimate,
        "rollback_options": plan.rollback_options or [],
        "expected_outcomes": plan.expected_outcomes or [],
        "approval_required": plan.approval_required,
        "approval_id": plan.approval_id,
        "graph_valid": plan.graph_valid,
        "graph_errors": plan.graph_errors or [],
        "project": plan.project,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
    }


def _run_to_dict(run) -> dict:
    return {
        "id": run.id,
        "plan_id": run.plan_id,
        "simulation_type": run.simulation_type,
        "counterfactual_description": run.counterfactual_description,
        "status": run.status,
        "paths": run.paths or [],
        "best_path_id": run.best_path_id,
        "success_probability": run.success_probability,
        "risk_score": run.risk_score,
        "confidence_score": run.confidence_score,
        "expected_failure_modes": run.expected_failure_modes or [],
        "tokens_used": run.tokens_used,
        "depth_reached": run.depth_reached,
        "actual_outcome": run.actual_outcome,
        "forecast_was_correct": run.forecast_was_correct,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }
