"""P14 Evaluation Harness tests.

Covers:
  - Eval runner loads and executes suites
  - Memory poisoning suite catches adversarial content
  - Retrieval suite detects isolation and quarantine
  - Trajectory suite verifies multi-session state
  - Worker stability suite runs idempotent passes
  - Token efficiency suite checks budget enforcement
  - Simulation forecasting suite checks confidence bounds
  - Release gate pass/fail logic
  - Report writing (JSON + Markdown)
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _uid(prefix: str = "t14") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ─── Runner / suite registry ──────────────────────────────────────────────────

def test_runner_all_suites_registered():
    from evals.suites import ALL_SUITES
    expected = {
        "memory_quality", "retrieval_quality", "trust_and_quarantine",
        "red_team", "trajectory", "worker_stability", "token_efficiency",
        "simulation_forecasting",
    }
    assert expected == set(ALL_SUITES.keys())


def test_runner_suites_are_instantiable():
    from evals.suites import ALL_SUITES
    from evals.base import EvalSuite
    for name, cls in ALL_SUITES.items():
        suite = cls()
        assert isinstance(suite, EvalSuite), f"{name} is not an EvalSuite"
        assert suite.NAME == name, f"{name}.NAME mismatch"


def test_eval_result_dataclass():
    from evals.base import EvalResult
    r = EvalResult(suite="foo", name="bar", passed=True, detail="ok")
    assert r.suite == "foo"
    assert r.passed is True
    assert r.critical is False


def test_eval_suite_helpers():
    from evals.suites.memory_quality import MemoryQualitySuite
    suite = MemoryQualitySuite()
    ok = suite._ok("test_name", "detail text")
    assert ok.passed is True
    assert ok.suite == "memory_quality"
    assert ok.name == "test_name"

    fail = suite._fail("fail_test", "bad thing happened")
    assert fail.passed is False
    assert fail.critical is False

    crit = suite._gate("critical_test", condition=False, detail="leakage")
    assert crit.passed is False
    assert crit.critical is True

    crit_pass = suite._gate("critical_pass", condition=True)
    assert crit_pass.passed is True
    assert crit_pass.critical is False


# ─── Memory quality suite ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_memory_quality_suite_runs(client):
    from evals.suites.memory_quality import MemoryQualitySuite
    results = await MemoryQualitySuite().run(client)
    assert len(results) > 0
    # Suite should produce named results
    names = {r.name for r in results}
    assert "episodic_store" in names
    assert "quarantine_blocks_poison" in names


@pytest.mark.asyncio
async def test_memory_quality_episodic_store_passes(client):
    from evals.suites.memory_quality import MemoryQualitySuite
    results = await MemoryQualitySuite().run(client)
    by_name = {r.name: r for r in results}
    assert by_name["episodic_store"].passed, by_name["episodic_store"].detail


@pytest.mark.asyncio
async def test_memory_quality_quarantine_poison_passes(client):
    from evals.suites.memory_quality import MemoryQualitySuite
    results = await MemoryQualitySuite().run(client)
    by_name = {r.name: r for r in results}
    assert by_name["quarantine_blocks_poison"].passed, by_name["quarantine_blocks_poison"].detail


@pytest.mark.asyncio
async def test_memory_quality_quarantine_excluded_from_recall(client):
    from evals.suites.memory_quality import MemoryQualitySuite
    results = await MemoryQualitySuite().run(client)
    by_name = {r.name: r for r in results}
    assert by_name["quarantine_excluded_from_recall"].passed, \
        by_name["quarantine_excluded_from_recall"].detail


# ─── Red-team suite ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_red_team_suite_runs(client):
    from evals.suites.red_team import RedTeamSuite
    results = await RedTeamSuite().run(client)
    assert len(results) >= 9  # 8 adversarial + cross-user + reactivation


@pytest.mark.asyncio
async def test_red_team_prompt_injection_quarantined(client):
    from evals.suites.red_team import RedTeamSuite
    results = await RedTeamSuite().run(client)
    r = next((x for x in results if x.name == "quarantine_prompt_injection"), None)
    assert r is not None, "quarantine_prompt_injection check missing"
    assert r.passed, r.detail


@pytest.mark.asyncio
async def test_red_team_approval_spoofing_quarantined(client):
    from evals.suites.red_team import RedTeamSuite
    results = await RedTeamSuite().run(client)
    r = next((x for x in results if x.name == "quarantine_approval_spoofing"), None)
    assert r is not None, "quarantine_approval_spoofing check missing"
    assert r.passed, r.detail


@pytest.mark.asyncio
async def test_red_team_tailscale_quarantined(client):
    from evals.suites.red_team import RedTeamSuite
    results = await RedTeamSuite().run(client)
    r = next((x for x in results if x.name == "quarantine_tailscale_manipulation"), None)
    assert r is not None, "quarantine_tailscale_manipulation check missing"
    assert r.passed, r.detail


@pytest.mark.asyncio
async def test_red_team_credential_quarantined(client):
    from evals.suites.red_team import RedTeamSuite
    results = await RedTeamSuite().run(client)
    r = next((x for x in results if x.name == "quarantine_credential_exposure"), None)
    assert r is not None, "quarantine_credential_exposure check missing"
    assert r.passed, r.detail


@pytest.mark.asyncio
async def test_red_team_cross_user_blocked(client):
    from evals.suites.red_team import RedTeamSuite
    results = await RedTeamSuite().run(client)
    r = next((x for x in results if x.name == "cross_user_recall_blocked"), None)
    assert r is not None, "cross_user_recall_blocked check missing"
    assert r.passed, r.detail


@pytest.mark.asyncio
async def test_red_team_no_critical_failures(client):
    from evals.suites.red_team import RedTeamSuite
    results = await RedTeamSuite().run(client)
    critical_fails = [r for r in results if not r.passed and r.critical]
    assert not critical_fails, \
        f"Red-team critical failures: {[(r.name, r.detail) for r in critical_fails]}"


# ─── Retrieval quality suite ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retrieval_quality_suite_runs(client):
    from evals.suites.retrieval_quality import RetrievalQualitySuite
    results = await RetrievalQualitySuite().run(client)
    assert len(results) >= 6


@pytest.mark.asyncio
async def test_retrieval_quarantine_exclusion_critical(client):
    from evals.suites.retrieval_quality import RetrievalQualitySuite
    results = await RetrievalQualitySuite().run(client)
    r = next((x for x in results if x.name == "quarantine_exclusion_rate"), None)
    assert r is not None, "quarantine_exclusion_rate check missing"
    assert r.passed, r.detail
    # Must not be a critical failure
    assert not (not r.passed and r.critical), "quarantine exclusion is a critical failure"


@pytest.mark.asyncio
async def test_retrieval_cross_user_isolation(client):
    from evals.suites.retrieval_quality import RetrievalQualitySuite
    results = await RetrievalQualitySuite().run(client)
    r = next((x for x in results if x.name == "cross_user_isolation"), None)
    assert r is not None, "cross_user_isolation check missing"
    assert r.passed, r.detail


@pytest.mark.asyncio
async def test_retrieval_project_isolation(client):
    from evals.suites.retrieval_quality import RetrievalQualitySuite
    results = await RetrievalQualitySuite().run(client)
    r = next((x for x in results if x.name == "project_isolation"), None)
    assert r is not None, "project_isolation check missing"
    assert r.passed, r.detail


# ─── Trust and quarantine suite ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_trust_suite_runs(client):
    from evals.suites.trust_and_quarantine import TrustAndQuarantineSuite
    results = await TrustAndQuarantineSuite().run(client)
    assert len(results) >= 8


@pytest.mark.asyncio
async def test_trust_positive_feedback_increases(client):
    from evals.suites.trust_and_quarantine import TrustAndQuarantineSuite
    results = await TrustAndQuarantineSuite().run(client)
    r = next((x for x in results if x.name == "positive_feedback_trust"), None)
    assert r is not None
    assert r.passed, r.detail


@pytest.mark.asyncio
async def test_trust_floor_bounded(client):
    from evals.suites.trust_and_quarantine import TrustAndQuarantineSuite
    results = await TrustAndQuarantineSuite().run(client)
    r = next((x for x in results if x.name == "trust_floor_bounded"), None)
    assert r is not None
    assert r.passed, r.detail


# ─── Trajectory suite ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_trajectory_suite_runs(client):
    from evals.suites.trajectory import TrajectorySuite
    results = await TrajectorySuite().run(client)
    assert len(results) >= 5


@pytest.mark.asyncio
async def test_trajectory_memories_loaded(client):
    from evals.suites.trajectory import TrajectorySuite
    results = await TrajectorySuite().run(client)
    r = next((x for x in results if x.name == "trajectory_loaded"), None)
    assert r is not None
    assert r.passed, r.detail


@pytest.mark.asyncio
async def test_trajectory_quarantine_persists(client):
    from evals.suites.trajectory import TrajectorySuite
    results = await TrajectorySuite().run(client)
    r = next((x for x in results if x.name == "quarantine_persists_across_sessions"), None)
    assert r is not None
    assert r.passed, r.detail


# ─── Worker stability suite ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_worker_stability_suite_runs(client):
    from evals.suites.worker_stability import WorkerStabilitySuite
    results = await WorkerStabilitySuite().run(client)
    assert len(results) >= 8


@pytest.mark.asyncio
async def test_worker_consolidation_runs(client):
    from evals.suites.worker_stability import WorkerStabilitySuite
    results = await WorkerStabilitySuite().run(client)
    r = next((x for x in results if x.name == "consolidation_runs"), None)
    assert r is not None
    assert r.passed, r.detail


@pytest.mark.asyncio
async def test_worker_graph_build_idempotent(client):
    from evals.suites.worker_stability import WorkerStabilitySuite
    results = await WorkerStabilitySuite().run(client)
    r = next((x for x in results if x.name == "graph_build_idempotent"), None)
    assert r is not None
    assert r.passed, r.detail


# ─── Token efficiency suite ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_token_budget_respected(client):
    from evals.suites.token_efficiency import TokenEfficiencySuite
    results = await TokenEfficiencySuite().run(client)
    r = next((x for x in results if x.name == "token_cost_within_budget"), None)
    assert r is not None
    assert r.passed, r.detail


@pytest.mark.asyncio
async def test_token_cost_reported(client):
    from evals.suites.token_efficiency import TokenEfficiencySuite
    results = await TokenEfficiencySuite().run(client)
    r = next((x for x in results if x.name == "token_cost_reported"), None)
    assert r is not None
    assert r.passed, r.detail


# ─── Simulation forecasting suite ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_simulation_confidence_bounds(client):
    from evals.suites.simulation_forecasting import SimulationForecastingSuite
    results = await SimulationForecastingSuite().run(client)
    r = next((x for x in results if x.name == "confidence_bounds"), None)
    assert r is not None
    assert r.passed, r.detail


@pytest.mark.asyncio
async def test_simulation_high_risk_auto_gated(client):
    from evals.suites.simulation_forecasting import SimulationForecastingSuite
    results = await SimulationForecastingSuite().run(client)
    r = next((x for x in results if x.name == "high_risk_auto_gated"), None)
    assert r is not None
    assert r.passed, r.detail


# ─── Release gate logic ───────────────────────────────────────────────────────

def test_release_gate_passes_clean_report():
    from evals.release_gate import _check_report
    report = {
        "critical_failures": [],
        "metrics": {
            "cross_user_leakage_rate": 0.0,
            "quarantine_exclusion_rate": 1.0,
        },
        "results": [],
    }
    passed, failures = _check_report(report)
    assert passed is True
    assert failures == []


def test_release_gate_fails_on_critical_failure():
    from evals.release_gate import _check_report
    report = {
        "critical_failures": ["red_team.quarantine_prompt_injection: not quarantined"],
        "metrics": {},
        "results": [],
    }
    passed, failures = _check_report(report)
    assert passed is False
    assert len(failures) >= 1


def test_release_gate_fails_on_leakage():
    from evals.release_gate import _check_report
    report = {
        "critical_failures": [],
        "metrics": {"cross_user_leakage_rate": 0.1},
        "results": [],
    }
    passed, failures = _check_report(report)
    assert passed is False
    assert any("cross_user_leakage" in f for f in failures)


def test_release_gate_fails_on_quarantine_miss():
    from evals.release_gate import _check_report
    report = {
        "critical_failures": [],
        "metrics": {"quarantine_exclusion_rate": 0.8},
        "results": [],
    }
    passed, failures = _check_report(report)
    assert passed is False
    assert any("quarantine_exclusion_rate" in f for f in failures)


def test_release_gate_fails_on_red_team_failure():
    from evals.release_gate import _check_report
    report = {
        "critical_failures": [],
        "metrics": {},
        "results": [
            {
                "suite": "red_team",
                "name": "quarantine_credential_exposure",
                "passed": False,
                "detail": "credential not quarantined",
            }
        ],
    }
    passed, failures = _check_report(report)
    assert passed is False
    assert any("red_team" in f for f in failures)


# ─── Report generation ─────────────────────────────────────────────────────────

def test_build_report():
    from evals.base import EvalResult
    from evals.runner import build_report
    results = [
        EvalResult(suite="memory_quality", name="test_a", passed=True),
        EvalResult(suite="red_team", name="test_b", passed=False, critical=True,
                   detail="bad thing"),
        EvalResult(suite="retrieval_quality", name="leakage", passed=True,
                   metric_name="cross_user_leakage_rate", metric_value=0.0),
    ]
    report = build_report(["memory_quality", "red_team", "retrieval_quality"], results, "2026-01-01T00:00:00")
    assert report.total == 3
    assert report.passed == 2
    assert report.failed == 1
    assert len(report.critical_failures) == 1
    assert report.metrics["cross_user_leakage_rate"] == 0.0
    assert not report.gate_passed  # has critical failure


def test_write_json_report(tmp_path):
    from evals.base import EvalResult
    from evals.runner import build_report, write_json
    results = [EvalResult(suite="test", name="check_a", passed=True)]
    report = build_report(["test"], results, "2026-01-01T00:00:00")
    out = tmp_path / "report.json"
    write_json(report, out)
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["total"] == 1
    assert data["passed"] == 1
    assert "metrics" in data
    assert "results" in data
    assert data["gate_passed"] is True


def test_write_markdown_report(tmp_path):
    from evals.base import EvalResult
    from evals.runner import build_report, write_markdown
    results = [
        EvalResult(suite="memory_quality", name="store", passed=True, detail="ok"),
        EvalResult(suite="red_team", name="injection", passed=False, detail="missed"),
    ]
    report = build_report(["memory_quality", "red_team"], results, "2026-01-01T00:00:00")
    out = tmp_path / "report.md"
    write_markdown(report, out)
    assert out.exists()
    md = out.read_text()
    assert "# Mimir Eval Report" in md
    assert "memory_quality" in md
    assert "red_team" in md
    assert "PASS" in md or "FAIL" in md
