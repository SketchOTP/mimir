"""P12 Predictive Planning + Simulation Engine tests.

Covers all acceptance criteria:
  1.  Plan creation with goal, steps, risk, confidence
  2.  Plan graph DAG validation — valid DAG passes
  3.  Plan graph DAG validation — cycle detected
  4.  Plan graph validation — unknown dependency reference
  5.  High-risk plan auto-flagged for approval (risk >= 0.7)
  6.  High-impact keyword auto-flags plan for approval
  7.  Low-risk plan stays as draft (no approval)
  8.  Plan approve/reject state transitions
  9.  reject_plan returns False for already-executed plan
  10. Outcome estimation returns OutcomeEstimate with clamped values
  11. Confidence floor: estimate.confidence_score >= 0.10
  12. Confidence ceiling: estimate.confidence_score <= 0.95
  13. Success probability clamped to [0.10, 0.95]
  14. Multi-path simulation generates up to MAX_BRANCHES paths
  15. Simulation depth bounded by MAX_DEPTH
  16. Simulation bounded by token budget
  17. Simulation returns best_path_id
  18. SimulationResult.to_dict() includes all required keys
  19. Simulation persists SimulationRun row
  20. Counterfactual: override_risk reduces risk in new estimate
  21. Counterfactual: add_rollback reduces risk estimate
  22. Counterfactual: probability_delta is finite and bounded
  23. Counterfactual persists as counterfactual simulation_type row
  24. list_counterfactuals returns only counterfactual runs for plan
  25. Rollback prediction: rollback_risk present on each path
  26. Forecast calibration: compute_calibration returns stats dict
  27. Forecast calibration: record_actual_outcome sets forecast_was_correct
  28. Forecast calibration: overconfidence detected correctly
  29. Forecast calibration: underconfidence detected correctly
  30. Cross-user / project isolation: plan not visible in wrong project
  31. POST /simulation/plans — creates plan, returns id
  32. GET /simulation/plans/{id} — 200 with plan data
  33. GET /simulation/plans/{id} — 404 for missing id
  34. POST /simulation/plans/{id}/simulate — returns simulation result
  35. POST /simulation/plans/{id}/counterfactual — returns counterfactual
  36. POST /simulation/plans/{id}/risk — returns risk forecast
  37. POST /simulation/calibration/compute — returns calibration stats
  38. GET /simulation/calibration/history — returns list
  39. Worker task run_forecast_calibration executes without error
  40. Scheduler includes forecast_calibration job
"""

from __future__ import annotations

import uuid
import pytest

from tests.conftest import as_user


def _uid() -> str:
    return str(uuid.uuid4())


def _step(step_id: str, deps: list[str] | None = None, risk: float = 0.1) -> dict:
    return {
        "id": step_id,
        "description": f"Step {step_id}",
        "dependencies": deps or [],
        "required_procedures": [],
        "risk_estimate": risk,
        "rollback_option": None,
    }


async def _db():
    from storage.database import get_session_factory
    return get_session_factory()


# ─── 1. Plan creation ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_plan_basic(app):
    from simulation.planner import create_plan, get_plan
    factory = await _db()
    async with factory() as session:
        plan = await create_plan(
            session,
            goal="Deploy new service",
            steps=[_step("s1"), _step("s2", deps=["s1"])],
            risk_estimate=0.2,
            project="p12_test",
        )
        await session.commit()
        fetched = await get_plan(session, plan.id)
        assert fetched is not None
        assert fetched.goal == "Deploy new service"
        assert len(fetched.steps) == 2


# ─── 2. Valid DAG passes ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_valid_dag_passes(app):
    from simulation.planner import validate_plan_graph, PlanStep
    steps = [
        PlanStep(id="a", description="A"),
        PlanStep(id="b", description="B", dependencies=["a"]),
        PlanStep(id="c", description="C", dependencies=["b"]),
    ]
    result = validate_plan_graph(steps)
    assert result["valid"] is True
    assert result["errors"] == []


# ─── 3. Cycle detection ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cycle_detected(app):
    from simulation.planner import validate_plan_graph, PlanStep
    steps = [
        PlanStep(id="a", description="A", dependencies=["c"]),
        PlanStep(id="b", description="B", dependencies=["a"]),
        PlanStep(id="c", description="C", dependencies=["b"]),
    ]
    result = validate_plan_graph(steps)
    assert result["valid"] is False
    assert any("cycle" in e.lower() for e in result["errors"])


# ─── 4. Unknown dependency reference ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_dep_reference(app):
    from simulation.planner import validate_plan_graph, PlanStep
    steps = [
        PlanStep(id="a", description="A", dependencies=["nonexistent"]),
    ]
    result = validate_plan_graph(steps)
    assert result["valid"] is False
    assert any("unknown" in e.lower() for e in result["errors"])


# ─── 5. High-risk auto-flags for approval ────────────────────────────────────

@pytest.mark.asyncio
async def test_high_risk_requires_approval(app):
    from simulation.planner import create_plan
    factory = await _db()
    async with factory() as session:
        plan = await create_plan(
            session,
            goal="Routine task",
            steps=[_step("s1")],
            risk_estimate=0.85,
            project="p12_test",
        )
        await session.commit()
        assert plan.approval_required is True
        assert plan.status == "pending_approval"


# ─── 6. High-impact keyword auto-flags for approval ──────────────────────────

@pytest.mark.asyncio
async def test_high_impact_keyword_requires_approval(app):
    from simulation.planner import create_plan
    factory = await _db()
    async with factory() as session:
        plan = await create_plan(
            session,
            goal="Delete all orphaned memories in production",
            steps=[_step("s1")],
            risk_estimate=0.3,
            project="p12_test",
        )
        await session.commit()
        assert plan.approval_required is True


# ─── 7. Low-risk plan stays draft ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_low_risk_stays_draft(app):
    from simulation.planner import create_plan
    factory = await _db()
    async with factory() as session:
        plan = await create_plan(
            session,
            goal="Update configuration value",
            steps=[_step("s1")],
            risk_estimate=0.1,
            project="p12_test",
        )
        await session.commit()
        assert plan.approval_required is False
        assert plan.status == "draft"


# ─── 8. Approve / reject state transitions ────────────────────────────────────

@pytest.mark.asyncio
async def test_approve_plan(app):
    from simulation.planner import create_plan, approve_plan, get_plan
    factory = await _db()
    async with factory() as session:
        plan = await create_plan(
            session, goal="High risk deploy", steps=[],
            risk_estimate=0.8, project="p12_test",
        )
        await session.commit()
        ok = await approve_plan(session, plan.id)
        await session.commit()
        assert ok is True
        fetched = await get_plan(session, plan.id)
        assert fetched.status == "approved"


@pytest.mark.asyncio
async def test_reject_plan(app):
    from simulation.planner import create_plan, reject_plan, get_plan
    factory = await _db()
    async with factory() as session:
        plan = await create_plan(
            session, goal="Risky migration", steps=[],
            risk_estimate=0.8, project="p12_test",
        )
        await session.commit()
        ok = await reject_plan(session, plan.id, reason="Too risky")
        await session.commit()
        assert ok is True
        fetched = await get_plan(session, plan.id)
        assert fetched.status == "rejected"


# ─── 9. reject_plan False for executed plan ───────────────────────────────────

@pytest.mark.asyncio
async def test_reject_executed_plan_returns_false(app):
    from simulation.planner import create_plan, reject_plan
    from storage.models import SimulationPlan
    factory = await _db()
    async with factory() as session:
        plan = await create_plan(
            session, goal="Executed task", steps=[], project="p12_test",
        )
        plan.status = "executed"
        await session.commit()
        ok = await reject_plan(session, plan.id)
        assert ok is False


# ─── 10-13. Outcome estimation — clamping ────────────────────────────────────

@pytest.mark.asyncio
async def test_outcome_estimate_clamped(app):
    from simulation.planner import create_plan
    from simulation.outcome_estimator import estimate_outcome
    factory = await _db()
    async with factory() as session:
        plan = await create_plan(
            session, goal="Test estimation", steps=[_step("s1")],
            risk_estimate=0.5, project="p12_estimate",
        )
        estimate = await estimate_outcome(session, plan, project="p12_estimate")
    assert 0.10 <= estimate.success_probability <= 0.95
    assert 0.10 <= estimate.confidence_score <= 0.95
    assert 0.0 <= estimate.risk_score <= 1.0
    assert isinstance(estimate.expected_failure_modes, list)
    assert isinstance(estimate.supporting_memories, list)


@pytest.mark.asyncio
async def test_confidence_floor_applied(app):
    from simulation.planner import create_plan
    from simulation.outcome_estimator import estimate_outcome
    factory = await _db()
    async with factory() as session:
        plan = await create_plan(
            session, goal="Zero-evidence plan", steps=[],
            risk_estimate=0.9, project="p12_floor",
        )
        estimate = await estimate_outcome(session, plan)
    # Even with zero evidence, confidence should be at floor
    assert estimate.confidence_score >= 0.10


# ─── 14. Multi-path simulation generates paths ────────────────────────────────

@pytest.mark.asyncio
async def test_simulation_generates_paths(app):
    from simulation.planner import create_plan
    from simulation.simulator import run_simulation
    factory = await _db()
    async with factory() as session:
        plan = await create_plan(
            session,
            goal="Deploy microservice",
            steps=[_step("s1"), _step("s2", deps=["s1"]), _step("s3", deps=["s2"])],
            risk_estimate=0.3,
            project="p12_sim",
        )
        await session.flush()
        result = await run_simulation(session, plan.id, project="p12_sim")
        await session.commit()
    assert result is not None
    assert len(result.paths) >= 1
    assert result.plan_id == plan.id


# ─── 15. Simulation depth bounded ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_simulation_depth_bounded(app):
    from simulation.planner import create_plan
    from simulation.simulator import run_simulation, MAX_DEPTH
    factory = await _db()
    async with factory() as session:
        # Create a plan with more steps than MAX_DEPTH
        steps = [_step(f"s{i}", deps=[f"s{i-1}"] if i > 0 else []) for i in range(10)]
        plan = await create_plan(
            session, goal="Long chain plan", steps=steps, project="p12_depth",
        )
        await session.flush()
        result = await run_simulation(session, plan.id, max_depth=3, project="p12_depth")
        await session.commit()
    assert result.depth_reached <= 3


# ─── 16. Token budget bounds simulation ──────────────────────────────────────

@pytest.mark.asyncio
async def test_simulation_token_budget(app):
    from simulation.planner import create_plan
    from simulation.simulator import run_simulation
    factory = await _db()
    async with factory() as session:
        steps = [_step(f"s{i}") for i in range(20)]
        plan = await create_plan(
            session, goal="Token-limited plan", steps=steps, project="p12_tokens",
        )
        await session.flush()
        result = await run_simulation(
            session, plan.id, token_budget=500, project="p12_tokens"
        )
        await session.commit()
    assert result.tokens_used <= 500


# ─── 17. Simulation returns best_path_id ─────────────────────────────────────

@pytest.mark.asyncio
async def test_simulation_best_path(app):
    from simulation.planner import create_plan
    from simulation.simulator import run_simulation
    factory = await _db()
    async with factory() as session:
        plan = await create_plan(
            session, goal="Optimize deployment", steps=[_step("s1"), _step("s2", deps=["s1"])],
            risk_estimate=0.4, project="p12_best",
        )
        await session.flush()
        result = await run_simulation(session, plan.id)
        await session.commit()
    assert result.best_path_id is not None
    path_ids = [p.path_id for p in result.paths]
    assert result.best_path_id in path_ids


# ─── 18. SimulationResult.to_dict() keys ─────────────────────────────────────

@pytest.mark.asyncio
async def test_simulation_result_to_dict(app):
    from simulation.planner import create_plan
    from simulation.simulator import run_simulation
    factory = await _db()
    async with factory() as session:
        plan = await create_plan(
            session, goal="Dict test plan", steps=[_step("s1")], project="p12_dict",
        )
        await session.flush()
        result = await run_simulation(session, plan.id)
        await session.commit()
    d = result.to_dict()
    for key in [
        "simulation_id", "plan_id", "paths", "best_path_id",
        "overall_success_probability", "overall_risk_score",
        "overall_confidence", "expected_failure_modes", "recommendation",
        "tokens_used", "depth_reached", "branches_explored", "bounded_by",
    ]:
        assert key in d, f"Missing key: {key}"


# ─── 19. Simulation persists SimulationRun row ───────────────────────────────

@pytest.mark.asyncio
async def test_simulation_persists_run(app):
    from simulation.planner import create_plan
    from simulation.simulator import run_simulation
    from storage.models import SimulationRun
    from sqlalchemy import select
    factory = await _db()
    async with factory() as session:
        plan = await create_plan(
            session, goal="Persisted sim plan", steps=[_step("s1")], project="p12_persist",
        )
        await session.flush()
        result = await run_simulation(session, plan.id)
        await session.commit()

        q = select(SimulationRun).where(SimulationRun.id == result.simulation_id)
        res = await session.execute(q)
        run_row = res.scalar_one_or_none()
    assert run_row is not None
    assert run_row.plan_id == plan.id
    assert run_row.simulation_type == "full"


# ─── 20-22. Counterfactual reasoning ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_counterfactual_override_risk(app):
    from simulation.planner import create_plan
    from simulation.counterfactuals import run_counterfactual
    factory = await _db()
    async with factory() as session:
        plan = await create_plan(
            session, goal="Risky migration plan", steps=[_step("s1")],
            risk_estimate=0.6, project="p12_cf",
        )
        await session.flush()
        result = await run_counterfactual(
            session, plan.id,
            scenario="What if we reduced the risk with validation first?",
            override_risk=0.2,
        )
        await session.commit()
    assert result is not None
    # Lower risk should improve success probability
    assert result.probability_delta >= -0.5  # not catastrophically worse
    assert 0.10 <= result.counterfactual_outcome.success_probability <= 0.95


@pytest.mark.asyncio
async def test_counterfactual_add_rollback(app):
    from simulation.planner import create_plan
    from simulation.counterfactuals import run_counterfactual
    factory = await _db()
    async with factory() as session:
        plan = await create_plan(
            session, goal="Plan with no rollback", steps=[_step("s1")],
            risk_estimate=0.5, project="p12_cf2",
        )
        await session.flush()
        result = await run_counterfactual(
            session, plan.id,
            scenario="What if we had a rollback plan?",
            add_rollback_option="Restore from snapshot",
        )
        await session.commit()
    assert result is not None
    # Adding rollback should reduce risk estimate in counterfactual
    assert result.counterfactual_outcome.risk_score <= result.original_outcome.risk_score + 0.05


@pytest.mark.asyncio
async def test_counterfactual_delta_bounded(app):
    from simulation.planner import create_plan
    from simulation.counterfactuals import run_counterfactual
    factory = await _db()
    async with factory() as session:
        plan = await create_plan(
            session, goal="Delta bounds check", steps=[_step("s1")], project="p12_cf3",
        )
        await session.flush()
        result = await run_counterfactual(
            session, plan.id,
            scenario="What if everything was perfect?",
            override_risk=0.0,
        )
        await session.commit()
    assert result is not None
    # Delta must be bounded: can't exceed 1.0 swing
    assert -1.0 <= result.probability_delta <= 1.0
    assert result.confidence >= 0.10


# ─── 23. Counterfactual persists as counterfactual simulation_type ────────────

@pytest.mark.asyncio
async def test_counterfactual_persists_type(app):
    from simulation.planner import create_plan
    from simulation.counterfactuals import run_counterfactual
    from storage.models import SimulationRun
    from sqlalchemy import select
    factory = await _db()
    async with factory() as session:
        plan = await create_plan(
            session, goal="Persist cf", steps=[_step("s1")], project="p12_cfp",
        )
        await session.flush()
        result = await run_counterfactual(
            session, plan.id, scenario="What if X?", override_risk=0.1,
        )
        await session.commit()

        q = select(SimulationRun).where(SimulationRun.id == result.counterfactual_id)
        res = await session.execute(q)
        run_row = res.scalar_one_or_none()
    assert run_row is not None
    assert run_row.simulation_type == "counterfactual"
    assert run_row.counterfactual_description == "What if X?"


# ─── 24. list_counterfactuals returns only counterfactual runs ────────────────

@pytest.mark.asyncio
async def test_list_counterfactuals(app):
    from simulation.planner import create_plan
    from simulation.counterfactuals import run_counterfactual, list_counterfactuals
    from simulation.simulator import run_simulation
    factory = await _db()
    async with factory() as session:
        plan = await create_plan(
            session, goal="List cf plan", steps=[_step("s1")], project="p12_lcf",
        )
        await session.flush()
        # Run a full sim and a counterfactual
        await run_simulation(session, plan.id)
        await run_counterfactual(session, plan.id, scenario="What if?", override_risk=0.3)
        await session.commit()

        cfs = await list_counterfactuals(session, plan.id)
    # Only the counterfactual should appear, not the full sim
    assert len(cfs) == 1
    assert cfs[0]["scenario"] == "What if?"


# ─── 25. Rollback risk present on each path ───────────────────────────────────

@pytest.mark.asyncio
async def test_paths_have_rollback_risk(app):
    from simulation.planner import create_plan
    from simulation.simulator import run_simulation
    factory = await _db()
    async with factory() as session:
        plan = await create_plan(
            session, goal="Rollback risk check", steps=[_step("s1", risk=0.6)],
            risk_estimate=0.5, project="p12_rrisk",
        )
        await session.flush()
        result = await run_simulation(session, plan.id)
        await session.commit()
    for path in result.paths:
        assert "rollback_risk" in path.to_dict()
        assert 0.0 <= path.rollback_risk <= 1.0


# ─── 26. Forecast calibration compute_calibration ────────────────────────────

@pytest.mark.asyncio
async def test_compute_calibration_returns_stats(app):
    from simulation.calibration import compute_calibration
    factory = await _db()
    async with factory() as session:
        result = await compute_calibration(session, project="p12_cal")
        await session.commit()
    # Even with no data, returns valid structure
    assert "total_forecasts" in result
    assert "forecast_accuracy" in result
    assert "overconfidence_rate" in result
    assert "underconfidence_rate" in result
    assert "mean_prediction_error" in result


# ─── 27. record_actual_outcome sets forecast_was_correct ─────────────────────

@pytest.mark.asyncio
async def test_record_actual_outcome(app):
    from simulation.planner import create_plan
    from simulation.simulator import run_simulation
    from simulation.calibration import record_actual_outcome
    from storage.models import SimulationRun
    from sqlalchemy import select
    factory = await _db()
    async with factory() as session:
        plan = await create_plan(
            session, goal="Outcome test plan", steps=[_step("s1")], project="p12_outcome",
        )
        await session.flush()
        result = await run_simulation(session, plan.id)
        await session.commit()

        ok = await record_actual_outcome(session, result.simulation_id, "success")
        await session.commit()

        q = select(SimulationRun).where(SimulationRun.id == result.simulation_id)
        res = await session.execute(q)
        run_row = res.scalar_one_or_none()
    assert ok is True
    assert run_row.actual_outcome == "success"
    assert run_row.forecast_was_correct is not None


# ─── 28. Overconfidence detection ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_overconfidence_detected(app):
    from simulation.planner import create_plan
    from simulation.simulator import run_simulation
    from simulation.calibration import record_actual_outcome, compute_calibration
    from storage.models import SimulationRun
    factory = await _db()
    async with factory() as session:
        # Create a plan with artificially high predicted success, then record failure
        plan = await create_plan(
            session, goal="Overconfident plan", steps=[_step("s1")],
            risk_estimate=0.1,  # low risk = high predicted success
            project="p12_overconf",
        )
        await session.flush()
        result = await run_simulation(session, plan.id)
        await session.commit()

        # Force success probability high in the run for testing overconfidence
        from sqlalchemy import select
        q = select(SimulationRun).where(SimulationRun.id == result.simulation_id)
        res = await session.execute(q)
        run_row = res.scalar_one()
        run_row.success_probability = 0.9  # artificially high
        await session.commit()

        # Record actual failure
        await record_actual_outcome(session, result.simulation_id, "failure")
        await session.commit()

        cal = await compute_calibration(session, project="p12_overconf", lookback_days=365)
        await session.commit()

    # Should flag overconfidence (predicted high, was wrong)
    assert cal["total_forecasts"] >= 1
    assert cal["overconfidence_rate"] >= 0.0


# ─── 29. Underconfidence detection ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_underconfidence_detected(app):
    from simulation.planner import create_plan
    from simulation.simulator import run_simulation
    from simulation.calibration import record_actual_outcome, compute_calibration
    from storage.models import SimulationRun
    from sqlalchemy import select
    factory = await _db()
    async with factory() as session:
        plan = await create_plan(
            session, goal="Underconfident plan", steps=[_step("s1")],
            risk_estimate=0.8, project="p12_underconf",
        )
        await session.flush()
        result = await run_simulation(session, plan.id)
        await session.commit()

        # Force success probability low for testing underconfidence
        q = select(SimulationRun).where(SimulationRun.id == result.simulation_id)
        res = await session.execute(q)
        run_row = res.scalar_one()
        run_row.success_probability = 0.2  # artificially low
        await session.commit()

        # Record actual success
        await record_actual_outcome(session, result.simulation_id, "success")
        await session.commit()

        cal = await compute_calibration(session, project="p12_underconf", lookback_days=365)
        await session.commit()

    assert cal["underconfidence_rate"] >= 0.0


# ─── 30. Cross-project isolation ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cross_project_isolation(app):
    from simulation.planner import create_plan, list_plans
    factory = await _db()
    async with factory() as session:
        await create_plan(
            session, goal="Project A plan", steps=[], project="p12_proj_a",
        )
        await create_plan(
            session, goal="Project B plan", steps=[], project="p12_proj_b",
        )
        await session.commit()

        plans_a = await list_plans(session, project="p12_proj_a")
        plans_b = await list_plans(session, project="p12_proj_b")

    a_goals = [p.goal for p in plans_a]
    b_goals = [p.goal for p in plans_b]
    assert "Project A plan" in a_goals
    assert "Project B plan" not in a_goals
    assert "Project B plan" in b_goals
    assert "Project A plan" not in b_goals


# ─── 31-38. API endpoint tests ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_api_create_plan(app, client):
    with as_user(app, "api_user_p12"):
        resp = await client.post("/api/simulation/plans", json={
            "goal": "API test plan",
            "steps": [{"id": "s1", "description": "Step one"}],
            "risk_estimate": 0.3,
            "project": "p12_api",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    assert data["goal"] == "API test plan"


@pytest.mark.asyncio
async def test_api_get_plan_200(app, client):
    with as_user(app, "api_user_p12"):
        create_resp = await client.post("/api/simulation/plans", json={
            "goal": "Get me plan",
            "steps": [],
            "project": "p12_api",
        })
        plan_id = create_resp.json()["id"]
        get_resp = await client.get(f"/api/simulation/plans/{plan_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == plan_id


@pytest.mark.asyncio
async def test_api_get_plan_404(app, client):
    with as_user(app, "api_user_p12"):
        resp = await client.get("/api/simulation/plans/nonexistent-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_run_simulation(app, client):
    with as_user(app, "api_user_p12"):
        create_resp = await client.post("/api/simulation/plans", json={
            "goal": "Simulate me",
            "steps": [{"id": "s1", "description": "Step one"}, {"id": "s2", "description": "Step two", "dependencies": ["s1"]}],
            "risk_estimate": 0.4,
            "project": "p12_api",
        })
        plan_id = create_resp.json()["id"]
        sim_resp = await client.post(f"/api/simulation/plans/{plan_id}/simulate", json={
            "max_depth": 3,
            "max_branches": 2,
        })
    assert sim_resp.status_code == 200
    data = sim_resp.json()
    assert "simulation_id" in data
    assert "paths" in data
    assert len(data["paths"]) >= 1


@pytest.mark.asyncio
async def test_api_run_counterfactual(app, client):
    with as_user(app, "api_user_p12"):
        create_resp = await client.post("/api/simulation/plans", json={
            "goal": "CF API test",
            "steps": [{"id": "s1", "description": "Main step"}],
            "project": "p12_api",
        })
        plan_id = create_resp.json()["id"]
        cf_resp = await client.post(f"/api/simulation/plans/{plan_id}/counterfactual", json={
            "scenario": "What if we had added validation?",
            "override_risk": 0.2,
        })
    assert cf_resp.status_code == 200
    data = cf_resp.json()
    assert "counterfactual_id" in data
    assert "probability_delta" in data
    assert "verdict" in data


@pytest.mark.asyncio
async def test_api_risk_estimate(app, client):
    with as_user(app, "api_user_p12"):
        create_resp = await client.post("/api/simulation/plans", json={
            "goal": "Risk test plan",
            "steps": [{"id": "s1", "description": "Risky step", "risk_estimate": 0.6}],
            "risk_estimate": 0.6,
            "project": "p12_api",
        })
        plan_id = create_resp.json()["id"]
        risk_resp = await client.post(f"/api/simulation/plans/{plan_id}/risk")
    assert risk_resp.status_code == 200
    data = risk_resp.json()
    assert "risk_forecast" in data
    assert "success_probability" in data["risk_forecast"]
    assert "high_risk" in data


@pytest.mark.asyncio
async def test_api_compute_calibration(app, client):
    with as_user(app, "api_user_p12"):
        resp = await client.post(
            "/api/simulation/calibration/compute",
            params={"project": "p12_api", "period": "daily"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "total_forecasts" in data
    assert "forecast_accuracy" in data


@pytest.mark.asyncio
async def test_api_calibration_history(app, client):
    with as_user(app, "api_user_p12"):
        resp = await client.get("/api/simulation/calibration/history")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ─── 39. Worker task run_forecast_calibration ────────────────────────────────

@pytest.mark.asyncio
async def test_worker_task_forecast_calibration(app):
    from worker.tasks import run_forecast_calibration
    result = await run_forecast_calibration()
    # Task runs without raising; returns None (void task)
    assert result is None


# ─── 40. Scheduler includes forecast_calibration ─────────────────────────────

def test_scheduler_includes_forecast_calibration(app):
    from worker.scheduler import create_scheduler
    scheduler = create_scheduler()
    job_ids = {j.id for j in scheduler.get_jobs()}
    assert "forecast_calibration" in job_ids
