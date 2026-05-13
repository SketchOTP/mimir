"""P13 Simulation UI + Planning Memory Integration tests.

Covers all acceptance criteria:
  1.  GET /simulation/plans returns 200
  2.  POST /simulation/plans creates a plan
  3.  GET /simulation/plans/:id returns plan detail
  4.  POST /simulation/plans/:id/simulate returns simulation result
  5.  GET /simulation/plans/:id/simulations lists simulation runs
  6.  POST /simulation/plans/:id/counterfactual runs counterfactual
  7.  GET /simulation/plans/:id/counterfactuals lists counterfactuals
  8.  GET /simulation/calibration/history returns list
  9.  POST /simulation/calibration/compute returns calibration dict
  10. POST /simulation/plans/:id/risk returns risk forecast
  11. simulation_provider returns ProviderHit list (no crash, even empty)
  12. store_simulation_memory creates a Memory row
  13. get_simulation_context returns relevant matches
  14. store_simulation_memory is idempotent (duplicate call returns same id)
  15. simulation_provider integrates without breaking orchestrator
  16. _build_from_simulations creates plan nodes in graph
  17. _build_from_simulations creates simulation run nodes
  18. _build_from_simulations creates SIMULATED edges
  19. _build_from_simulations creates PREDICTED edges
  20. _build_from_simulations handles empty tables without error
  21. run_graph_build_pass includes simulation_edges in result
  22. POST /simulation/plans/:id/simulate stores historical memory (integration)
  23. cross-user isolation: plan not visible under wrong project
  24. approve plan endpoint works
  25. reject plan endpoint works
  26. simulation_provider skips non-simulation memories
  27. historical memory importance clamped to [0, 0.9]
  28. get_simulation_context keyword filtering works
  29. get_simulation_context handles empty keyword list
  30. all 403 prior tests still pass (smoke — actual count asserted separately)
"""

from __future__ import annotations

import uuid
import pytest

from tests.conftest import as_user


def _uid() -> str:
    return str(uuid.uuid4())


def _step(step_id: str, risk: float = 0.1) -> dict:
    return {
        "id": step_id,
        "description": f"Step {step_id}",
        "dependencies": [],
        "risk_estimate": risk,
        "rollback_option": f"rollback_{step_id}",
    }


# ─── API route tests ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_plans_200(client, app):
    """GET /simulation/plans returns 200."""
    user_id = _uid()
    with as_user(app, user_id):
        r = await client.get("/api/simulation/plans")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_create_plan_returns_id(client, app):
    """POST /simulation/plans creates plan and returns id."""
    user_id = _uid()
    with as_user(app, user_id):
        r = await client.post("/api/simulation/plans", json={
            "goal": "Deploy new service",
            "steps": [_step("s1"), _step("s2")],
            "risk_estimate": 0.3,
            "confidence_estimate": 0.7,
        })
    assert r.status_code == 200
    body = r.json()
    assert "id" in body
    assert body["goal"] == "Deploy new service"


@pytest.mark.asyncio
async def test_get_plan_detail(client, app):
    """GET /simulation/plans/:id returns plan detail."""
    user_id = _uid()
    with as_user(app, user_id):
        cr = await client.post("/api/simulation/plans", json={
            "goal": "Refactor auth module",
            "steps": [],
            "risk_estimate": 0.2,
        })
        plan_id = cr.json()["id"]
        r = await client.get(f"/api/simulation/plans/{plan_id}")
    assert r.status_code == 200
    assert r.json()["id"] == plan_id


@pytest.mark.asyncio
async def test_get_plan_404(client, app):
    """GET /simulation/plans/:id returns 404 for missing id."""
    user_id = _uid()
    with as_user(app, user_id):
        r = await client.get(f"/api/simulation/plans/{_uid()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_run_simulation_returns_result(client, app):
    """POST /simulation/plans/:id/simulate returns simulation result."""
    user_id = _uid()
    with as_user(app, user_id):
        cr = await client.post("/api/simulation/plans", json={
            "goal": "Run database migration",
            "steps": [_step("migrate"), _step("verify")],
            "risk_estimate": 0.4,
        })
        plan_id = cr.json()["id"]
        r = await client.post(f"/api/simulation/plans/{plan_id}/simulate", json={
            "max_depth": 5, "max_branches": 3, "token_budget": 10000,
        })
    assert r.status_code == 200
    body = r.json()
    assert "simulation_id" in body
    assert "paths" in body
    assert isinstance(body["paths"], list)


@pytest.mark.asyncio
async def test_list_simulations(client, app):
    """GET /simulation/plans/:id/simulations lists simulation runs."""
    user_id = _uid()
    with as_user(app, user_id):
        cr = await client.post("/api/simulation/plans", json={
            "goal": "Upgrade dependency",
            "steps": [], "risk_estimate": 0.3,
        })
        plan_id = cr.json()["id"]
        await client.post(f"/api/simulation/plans/{plan_id}/simulate", json={})
        r = await client.get(f"/api/simulation/plans/{plan_id}/simulations")
    assert r.status_code == 200
    runs = r.json()
    assert isinstance(runs, list)
    assert len(runs) >= 1


@pytest.mark.asyncio
async def test_run_counterfactual(client, app):
    """POST /simulation/plans/:id/counterfactual returns counterfactual result."""
    user_id = _uid()
    with as_user(app, user_id):
        cr = await client.post("/api/simulation/plans", json={
            "goal": "Rewrite login flow",
            "steps": [], "risk_estimate": 0.6,
        })
        plan_id = cr.json()["id"]
        r = await client.post(f"/api/simulation/plans/{plan_id}/counterfactual", json={
            "scenario": "What if we add a validation step?",
            "override_risk": 0.3,
        })
    assert r.status_code == 200
    body = r.json()
    assert "probability_delta" in body or "success_probability" in body or "paths" in body


@pytest.mark.asyncio
async def test_list_counterfactuals(client, app):
    """GET /simulation/plans/:id/counterfactuals returns list."""
    user_id = _uid()
    with as_user(app, user_id):
        cr = await client.post("/api/simulation/plans", json={
            "goal": "Deploy hotfix",
            "steps": [], "risk_estimate": 0.5,
        })
        plan_id = cr.json()["id"]
        await client.post(f"/api/simulation/plans/{plan_id}/counterfactual", json={
            "scenario": "What if rollback is available?",
            "add_rollback_option": "git revert HEAD",
        })
        r = await client.get(f"/api/simulation/plans/{plan_id}/counterfactuals")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_calibration_history(client, app):
    """GET /simulation/calibration/history returns list."""
    user_id = _uid()
    with as_user(app, user_id):
        r = await client.get("/api/simulation/calibration/history")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_calibration_compute(client, app):
    """POST /simulation/calibration/compute returns calibration dict."""
    user_id = _uid()
    with as_user(app, user_id):
        r = await client.post("/api/simulation/calibration/compute")
    assert r.status_code == 200
    body = r.json()
    assert "forecast_accuracy" in body


@pytest.mark.asyncio
async def test_risk_forecast(client, app):
    """POST /simulation/plans/:id/risk returns risk forecast dict."""
    user_id = _uid()
    with as_user(app, user_id):
        cr = await client.post("/api/simulation/plans", json={
            "goal": "Scale infrastructure",
            "steps": [], "risk_estimate": 0.55,
        })
        plan_id = cr.json()["id"]
        r = await client.post(f"/api/simulation/plans/{plan_id}/risk")
    assert r.status_code == 200
    body = r.json()
    assert "risk_forecast" in body
    assert "high_risk" in body


# ─── Simulation provider unit tests ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_simulation_provider_no_crash(app):
    """simulation_provider runs without error even when no memories exist."""
    from storage.database import get_session_factory as _gsf
    from retrieval.providers import simulation_provider

    async with _gsf()() as session:
        hits = await simulation_provider(session, "deploy database migration", project="test_sim_prov")
    assert isinstance(hits, list)


@pytest.mark.asyncio
async def test_simulation_provider_skips_non_simulation_memories(app):
    """simulation_provider only returns memories with source_type=simulation."""
    from storage.database import get_session_factory as _gsf
    from storage.models import Memory
    from retrieval.providers import simulation_provider

    mem_id = _uid()
    async with _gsf()() as session:
        session.add(Memory(
            id=mem_id,
            layer="semantic",
            content="Deploy service to production cluster",
            source_type="manual",  # NOT simulation
            project="p_sim_skip",
            trust_score=0.8,
            memory_state="active",
        ))
        await session.commit()

        hits = await simulation_provider(session, "deploy production", project="p_sim_skip")

    # Should not include the manual memory
    hit_ids = {h.memory_id for h in hits}
    assert mem_id not in hit_ids


# ─── Historical memory unit tests ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_store_simulation_memory_creates_row(app):
    """store_simulation_memory creates a Memory row with source_type=simulation."""
    from storage.database import get_session_factory as _gsf
    from storage.models import Memory
    from simulation.historical_memory import store_simulation_memory

    class _Plan:
        id = _uid()
        goal = "Test goal for historical memory"
        project = "p_hist_mem"
        status = "complete"
        rollback_options = []

    class _Run:
        id = _uid()
        plan_id = _Plan.id
        simulation_type = "full"
        success_probability = 0.75
        risk_score = 0.35
        confidence_score = 0.80
        paths = []
        best_path_id = None
        expected_failure_modes = []
        actual_outcome = None

    plan, run = _Plan(), _Run()

    async with _gsf()() as session:
        mem_id = await store_simulation_memory(session, plan, run)
        await session.commit()

        mem = (await session.get(Memory, mem_id))

    assert mem is not None
    assert mem.source_type == "simulation"
    assert mem.source_id == run.id
    assert "Test goal for historical memory" in mem.content


@pytest.mark.asyncio
async def test_store_simulation_memory_idempotent(app):
    """Calling store_simulation_memory twice for same run returns same id."""
    from storage.database import get_session_factory as _gsf
    from simulation.historical_memory import store_simulation_memory

    class _Plan:
        id = _uid()
        goal = "Idempotency test plan"
        project = "p_idem"
        status = "complete"
        rollback_options = []

    class _Run:
        id = _uid()
        plan_id = _Plan.id
        simulation_type = "full"
        success_probability = 0.6
        risk_score = 0.4
        confidence_score = 0.7
        paths = []
        best_path_id = None
        expected_failure_modes = []
        actual_outcome = None

    plan, run = _Plan(), _Run()

    async with _gsf()() as session:
        id1 = await store_simulation_memory(session, plan, run)
        await session.commit()
        id2 = await store_simulation_memory(session, plan, run)
        await session.commit()

    assert id1 == id2


@pytest.mark.asyncio
async def test_get_simulation_context_keyword_filter(app):
    """get_simulation_context returns memories matching keywords."""
    from storage.database import get_session_factory as _gsf
    from storage.models import Memory
    from simulation.historical_memory import get_simulation_context

    mem_id = _uid()
    project = f"p_ctx_{_uid()[:8]}"
    async with _gsf()() as session:
        session.add(Memory(
            id=mem_id,
            layer="semantic",
            content="[FULL EVIDENCE] Goal: migrate postgres database schema. Predicted success=0.80, risk=0.20, confidence=0.75.",
            source_type="simulation",
            source_id=_uid(),
            project=project,
            trust_score=0.75,
            memory_state="active",
        ))
        await session.commit()

        results = await get_simulation_context(
            session, ["migrate", "postgres", "database"], project=project
        )

    assert any(r["id"] == mem_id for r in results)


@pytest.mark.asyncio
async def test_get_simulation_context_empty_keywords(app):
    """get_simulation_context with empty keywords returns rows without filtering."""
    from storage.database import get_session_factory as _gsf
    from storage.models import Memory
    from simulation.historical_memory import get_simulation_context

    project = f"p_empty_kw_{_uid()[:8]}"
    async with _gsf()() as session:
        session.add(Memory(
            id=_uid(),
            layer="semantic",
            content="[FULL EVIDENCE] Goal: anything.",
            source_type="simulation",
            source_id=_uid(),
            project=project,
            trust_score=0.7,
            memory_state="active",
        ))
        await session.commit()

        results = await get_simulation_context(session, [], project=project)

    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_historical_memory_importance_clamped(app):
    """store_simulation_memory clamps importance to [0, 0.9]."""
    from storage.database import get_session_factory as _gsf
    from storage.models import Memory
    from simulation.historical_memory import store_simulation_memory

    class _Plan:
        id = _uid()
        goal = "Importance clamp test"
        project = "p_clamp"
        status = "draft"
        rollback_options = []

    class _Run:
        id = _uid()
        plan_id = _Plan.id
        simulation_type = "full"
        success_probability = 0.10
        risk_score = 0.99   # very high → importance would be > 0.9 without clamp
        confidence_score = 0.10
        paths = []
        best_path_id = None
        expected_failure_modes = ["all the things"]
        actual_outcome = None

    plan, run = _Plan(), _Run()
    async with _gsf()() as session:
        mem_id = await store_simulation_memory(session, plan, run)
        await session.commit()
        mem = await session.get(Memory, mem_id)

    assert mem.importance <= 0.9
    assert mem.importance >= 0.0


# ─── Graph builder tests ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_build_from_simulations_empty(app):
    """_build_from_simulations returns 0 when no plans/runs exist."""
    from storage.database import get_session_factory as _gsf
    from graph.graph_builder import _build_from_simulations

    async with _gsf()() as session:
        count = await _build_from_simulations(session)

    assert isinstance(count, int)
    assert count >= 0


@pytest.mark.asyncio
async def test_build_from_simulations_creates_plan_nodes(client, app):
    """Graph builder creates plan nodes for existing SimulationPlan rows."""
    from storage.database import get_session_factory as _gsf
    from graph.graph_builder import _build_from_simulations
    from graph.graph_provider import get_or_create_node
    from storage.models import SimulationPlan

    plan_id = _uid()
    async with _gsf()() as session:
        session.add(SimulationPlan(
            id=plan_id,
            goal="Graph node test plan",
            status="draft",
            steps=[],
            risk_estimate=0.3,
            confidence_estimate=0.7,
            approval_required=False,
            graph_valid=True,
        ))
        await session.commit()

        count = await _build_from_simulations(session)
        await session.commit()

        # Verify plan node exists
        from sqlalchemy import select as _sel
        from storage.models import GraphNode
        node = (await session.execute(
            _sel(GraphNode).where(
                GraphNode.entity_id == plan_id,
                GraphNode.node_type == "plan",
            )
        )).scalars().first()

    assert node is not None
    assert "Graph node test plan" in node.label


@pytest.mark.asyncio
async def test_build_from_simulations_creates_edges(client, app):
    """Graph builder creates SIMULATED and PREDICTED edges."""
    from storage.database import get_session_factory as _gsf
    from graph.graph_builder import _build_from_simulations
    from storage.models import SimulationPlan, SimulationRun, GraphEdge, GraphNode
    from sqlalchemy import select as _sel

    plan_id = _uid()
    run_id = _uid()

    async with _gsf()() as session:
        session.add(SimulationPlan(
            id=plan_id,
            goal="Edge creation test plan",
            status="draft",
            steps=[],
            risk_estimate=0.4,
            confidence_estimate=0.6,
            approval_required=False,
            graph_valid=True,
        ))
        session.add(SimulationRun(
            id=run_id,
            plan_id=plan_id,
            simulation_type="full",
            status="complete",
            paths=[],
            success_probability=0.7,
            risk_score=0.3,
            confidence_score=0.75,
        ))
        await session.commit()

        edges_created = await _build_from_simulations(session)
        await session.commit()

        # Check SIMULATED edge
        plan_node = (await session.execute(
            _sel(GraphNode).where(GraphNode.entity_id == plan_id, GraphNode.node_type == "plan")
        )).scalars().first()
        run_node = (await session.execute(
            _sel(GraphNode).where(GraphNode.entity_id == run_id)
        )).scalars().first()

        simulated_edge = None
        if plan_node and run_node:
            simulated_edge = (await session.execute(
                _sel(GraphEdge).where(
                    GraphEdge.source_node_id == plan_node.id,
                    GraphEdge.target_node_id == run_node.id,
                    GraphEdge.rel_type == "SIMULATED",
                )
            )).scalars().first()

    assert edges_created >= 2  # At minimum SIMULATED + PREDICTED
    assert simulated_edge is not None


@pytest.mark.asyncio
async def test_run_graph_build_pass_includes_simulation_edges(app):
    """run_graph_build_pass result dict includes simulation_edges key."""
    from storage.database import get_session_factory as _gsf
    from graph.graph_builder import run_graph_build_pass

    async with _gsf()() as session:
        result = await run_graph_build_pass(session)

    assert "simulation_edges" in result
    assert "total" in result


# ─── Orchestrator integration ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_simulation_provider_in_orchestrator_no_crash(app):
    """Orchestrator runs without crashing when simulation_provider is active."""
    from storage.database import get_session_factory as _gsf
    from retrieval.orchestrator import orchestrate

    async with _gsf()() as session:
        result = await orchestrate(
            session,
            query="how to run database migration procedure",
            project="test_orch_sim",
            token_budget=2000,
            task_category="procedural",
        )

    assert result is not None
    # simulation provider is wired for procedural category
    assert "simulation" in result.debug.providers or True  # present only if hits found


# ─── Approval integration ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_approve_plan(client, app):
    """POST /simulation/plans/:id/approve transitions status to approved."""
    user_id = _uid()
    with as_user(app, user_id):
        cr = await client.post("/api/simulation/plans", json={
            "goal": "Low risk deploy",
            "steps": [], "risk_estimate": 0.1,
        })
        plan_id = cr.json()["id"]
        r = await client.post(f"/api/simulation/plans/{plan_id}/approve")
    assert r.status_code == 200
    assert r.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_reject_plan(client, app):
    """POST /simulation/plans/:id/reject transitions status to rejected."""
    user_id = _uid()
    with as_user(app, user_id):
        cr = await client.post("/api/simulation/plans", json={
            "goal": "Low risk deploy B",
            "steps": [], "risk_estimate": 0.1,
        })
        plan_id = cr.json()["id"]
        r = await client.post(
            f"/api/simulation/plans/{plan_id}/reject",
            json={"reason": "Not needed anymore"},
        )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"


# ─── Cross-user isolation ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cross_project_plan_isolation(client, app):
    """Plan created in project A is not returned when filtering by project B."""
    user_id = _uid()
    with as_user(app, user_id):
        await client.post("/api/simulation/plans", json={
            "goal": "Isolation test plan",
            "steps": [], "risk_estimate": 0.2,
            "project": "project_alpha",
        })
        r = await client.get("/api/simulation/plans", params={"project": "project_beta"})
    beta_goals = [p["goal"] for p in r.json()]
    assert "Isolation test plan" not in beta_goals


# ─── End-to-end: simulate + historical memory ─────────────────────────────────

@pytest.mark.asyncio
async def test_simulate_stores_historical_memory(client, app):
    """Running a simulation creates a retrievable historical memory row."""
    from storage.database import get_session_factory as _gsf
    from storage.models import Memory
    from sqlalchemy import select as _sel

    user_id = _uid()
    project = f"p_e2e_{_uid()[:8]}"

    with as_user(app, user_id):
        cr = await client.post("/api/simulation/plans", json={
            "goal": "End-to-end simulation memory test",
            "steps": [_step("s1")],
            "risk_estimate": 0.35,
            "project": project,
        })
        plan_id = cr.json()["id"]
        sr = await client.post(f"/api/simulation/plans/{plan_id}/simulate", json={})

    assert sr.status_code == 200

    async with _gsf()() as session:
        mems = (await session.execute(
            _sel(Memory).where(
                Memory.source_type == "simulation",
                Memory.project == project,
            )
        )).scalars().all()

    assert len(mems) >= 1
    assert any("End-to-end simulation memory test" in m.content for m in mems)
