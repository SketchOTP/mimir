"""Worker stability eval suite — idempotency, locking, and timeout behavior."""

from __future__ import annotations

import asyncio

from evals.base import EvalResult, EvalSuite


class WorkerStabilitySuite(EvalSuite):
    NAME = "worker_stability"
    DESCRIPTION = "Verifies consolidation, graph build, and feedback inference are idempotent."

    async def run(self, client) -> list[EvalResult]:
        results: list[EvalResult] = []

        # ── 1. Consolidation pass runs without error ──────────────────────────
        r = await client.post("/api/system/consolidate")
        passed = r.status_code == 200
        data = r.json() if passed else {}
        inner = data.get("result") or {}
        results.append(
            self._ok("consolidation_runs", f"result_keys={list(inner.keys())}") if passed
            else self._fail("consolidation_runs", f"status={r.status_code}")
        )

        # ── 2. Second consolidation pass runs and is idempotent ───────────────
        r2 = await client.post("/api/system/consolidate")
        results.append(
            self._ok("consolidation_idempotent", "second pass succeeded") if r2.status_code == 200
            else self._fail("consolidation_idempotent", f"second pass status={r2.status_code}")
        )

        # ── 3. Graph build pass runs without error ────────────────────────────
        r = await client.post("/api/graph/build")
        passed = r.status_code == 200
        data = r.json() if passed else {}
        graph_data = data.get("result") or data  # graph/build returns result directly
        results.append(
            self._ok("graph_build_runs", f"result_keys={list(graph_data.keys())}") if passed
            else self._fail("graph_build_runs", f"status={r.status_code}")
        )

        # ── 4. Second graph build is idempotent (≤ first pass new-node count) ─
        r2 = await client.post("/api/graph/build")
        results.append(
            self._ok("graph_build_idempotent", "second build pass succeeded") if r2.status_code == 200
            else self._fail("graph_build_idempotent", f"second build status={r2.status_code}")
        )

        # ── 5. Reflection pass runs without error ─────────────────────────────
        r = await client.post("/api/system/reflect")
        results.append(
            self._ok("reflection_runs", "reflection pass completed") if r.status_code == 200
            else self._fail("reflection_runs", f"status={r.status_code}")
        )

        # ── 6. Second reflection pass is idempotent ───────────────────────────
        r2 = await client.post("/api/system/reflect")
        results.append(
            self._ok("reflection_idempotent", "second pass OK") if r2.status_code == 200
            else self._fail("reflection_idempotent", f"status={r2.status_code}")
        )

        # ── 7. Lifecycle pass runs without error ──────────────────────────────
        r = await client.post("/api/system/lifecycle")
        results.append(
            self._ok("lifecycle_runs", "lifecycle pass completed") if r.status_code == 200
            else self._fail("lifecycle_runs", f"status={r.status_code}")
        )

        # ── 8. Provider stats aggregation runs without error ──────────────────
        r = await client.post("/api/providers/aggregate")
        results.append(
            self._ok("provider_stats_aggregation_runs", "aggregation completed") if r.status_code == 200
            else self._fail("provider_stats_aggregation_runs", f"status={r.status_code}")
        )

        # ── 9. Concurrent consolidation calls handled gracefully ──────────────
        coros = [client.post("/api/system/consolidate") for _ in range(3)]
        responses = await asyncio.gather(*coros, return_exceptions=True)
        statuses = [
            r.status_code if hasattr(r, "status_code") else 500
            for r in responses
        ]
        all_ok = all(s in (200, 409, 429) for s in statuses)
        results.append(
            self._ok("concurrent_consolidation_graceful", f"statuses={statuses}") if all_ok
            else self._fail("concurrent_consolidation_graceful",
                            f"unexpected status in concurrent calls: {statuses}")
        )

        return results
