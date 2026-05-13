"""Simulation and forecast calibration eval suite."""

from __future__ import annotations

from evals.base import EvalResult, EvalSuite
from evals.fixtures import uid


class SimulationForecastingSuite(EvalSuite):
    NAME = "simulation_forecasting"
    DESCRIPTION = "Verifies simulation engine confidence bounds, calibration, and approval gating."

    async def run(self, client) -> list[EvalResult]:
        results: list[EvalResult] = []

        # ── 1. Plan creation with steps ───────────────────────────────────────
        r = await client.post("/api/simulation/plans", json={
            "goal": f"Deploy service update {uid()}",
            "steps": [
                {
                    "id": "s1",
                    "description": "Run integration tests",
                    "risk_estimate": 0.1,
                    "rollback_option": "skip and revert",
                    "dependencies": [],
                },
                {
                    "id": "s2",
                    "description": "Deploy to staging",
                    "risk_estimate": 0.3,
                    "rollback_option": "redeploy previous version",
                    "dependencies": ["s1"],
                },
            ],
        })
        plan_id = None
        if r.status_code == 200:
            plan_id = r.json().get("id")
            results.append(self._ok("plan_creation", f"plan_id={plan_id}"))
        else:
            results.append(self._fail("plan_creation", f"status={r.status_code}"))

        # ── 2. Simulation runs and returns paths ──────────────────────────────
        if plan_id:
            r2 = await client.post(f"/api/simulation/plans/{plan_id}/simulate", json={})
            if r2.status_code == 200:
                run = r2.json()
                has_paths = len(run.get("paths", [])) > 0
                results.append(
                    self._ok("simulation_runs", f"paths={len(run.get('paths', []))}") if has_paths
                    else self._fail("simulation_runs", "no paths returned")
                )

                # ── 3. Confidence in [0.1, 0.95] ─────────────────────────────
                confidence = run.get("best_path", {}).get("success_probability", -1)
                if confidence == -1 and run.get("paths"):
                    confidence = run["paths"][0].get("success_probability", -1)
                in_bounds = 0.1 <= confidence <= 0.95
                results.append(
                    self._ok("confidence_bounds", f"confidence={confidence:.3f}") if in_bounds
                    else self._fail("confidence_bounds",
                                   f"confidence={confidence} outside [0.1, 0.95]")
                )

                run_id = run.get("id")
            else:
                results.append(self._fail("simulation_runs", f"status={r2.status_code}"))
                results.append(self._fail("confidence_bounds", "skipped: simulation failed"))
                run_id = None
        else:
            results.append(self._fail("simulation_runs", "skipped: plan creation failed"))
            results.append(self._fail("confidence_bounds", "skipped: plan creation failed"))
            run_id = None

        # ── 4. High-risk plan auto-gated for approval ─────────────────────────
        r = await client.post("/api/simulation/plans", json={
            "goal": f"Delete all production data and replace database {uid()}",
            "risk_estimate": 0.9,
            "steps": [
                {
                    "id": "s1",
                    "description": "Drop all production tables",
                    "risk_estimate": 0.95,
                    "rollback_option": "restore from backup",
                    "dependencies": [],
                },
            ],
        })
        if r.status_code == 200:
            plan_data = r.json()
            approval_required = plan_data.get("approval_required", False)
            status = plan_data.get("status", "")
            auto_gated = approval_required or status == "pending_approval"
            results.append(
                self._ok("high_risk_auto_gated",
                         f"approval_required={approval_required}, status={status}") if auto_gated
                else self._fail("high_risk_auto_gated",
                               f"high-risk plan not gated: approval_required={approval_required}")
            )
        else:
            results.append(self._fail("high_risk_auto_gated", f"status={r.status_code}"))

        # ── 5. Outcome recording works ────────────────────────────────────────
        if run_id:
            r = await client.post(f"/api/simulation/runs/{run_id}/outcome", json={
                "actual_outcome": "success",
            })
            results.append(
                self._ok("outcome_recording", "outcome recorded") if r.status_code == 200
                else self._fail("outcome_recording", f"status={r.status_code}")
            )

            # ── 6. Calibration computes after outcome recorded ─────────────────
            r2 = await client.post("/api/simulation/calibration/compute")
            if r2.status_code == 200:
                cal = r2.json()
                has_accuracy = "forecast_accuracy" in cal
                results.append(
                    self._ok("calibration_computes",
                             f"forecast_accuracy={cal.get('forecast_accuracy')}") if has_accuracy
                    else self._fail("calibration_computes", f"missing forecast_accuracy in {list(cal.keys())}")
                )
            else:
                results.append(self._fail("calibration_computes", f"status={r2.status_code}"))
        else:
            results.append(self._fail("outcome_recording", "skipped: no run_id"))
            results.append(self._fail("calibration_computes", "skipped: no run_id"))

        # ── 7. Risk estimate endpoint works (requires plan_id) ───────────────
        if plan_id:
            r = await client.post(f"/api/simulation/plans/{plan_id}/risk")
            if r.status_code == 200:
                risk = r.json()
                has_score = "risk_score" in risk or "success_probability" in risk
                results.append(
                    self._ok("risk_estimate_works", f"response_keys={list(risk.keys())}") if has_score
                    else self._fail("risk_estimate_works", f"unexpected response: {list(risk.keys())}")
                )
            else:
                results.append(self._fail("risk_estimate_works", f"status={r.status_code}"))
        else:
            results.append(self._fail("risk_estimate_works", "skipped: no plan_id"))

        return results
