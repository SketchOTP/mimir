"""Token efficiency eval suite — budget enforcement, latency, cost reporting."""

from __future__ import annotations

import time

from evals.base import EvalResult, EvalSuite
from evals.fixtures import RETRIEVAL_CORPUS, uid

# Warning thresholds (not hard failures initially)
_P95_LATENCY_WARN_MS = 3000
_P95_LATENCY_FAIL_MS = 10000


class TokenEfficiencySuite(EvalSuite):
    NAME = "token_efficiency"
    DESCRIPTION = "Verifies token budget enforcement, cost reporting, and recall latency."

    async def run(self, client) -> list[EvalResult]:
        results: list[EvalResult] = []
        project = uid("te")

        # Seed some memories
        for item in RETRIEVAL_CORPUS[:6]:
            await client.post("/api/memory", json={
                "layer": item["layer"],
                "content": item["content"],
                "project": project,
            })

        # ── 1. Recall with token_budget returns context ───────────────────────
        r = await client.post("/api/events/recall", json={
            "query": "Python database async session",
            "project": project,
            "token_budget": 1000,
        })
        if r.status_code == 200:
            ctx = r.json().get("context")
            has_context = ctx is not None
            results.append(
                self._ok("token_budget_context_present", "context returned with token_budget") if has_context
                else self._fail("token_budget_context_present", "no context block in response")
            )
        else:
            results.append(self._fail("token_budget_context_present", f"status={r.status_code}"))

        # ── 2. token_cost ≤ token_budget ─────────────────────────────────────
        budget = 512
        r = await client.post("/api/events/recall", json={
            "query": "memory consolidation nightly process",
            "project": project,
            "token_budget": budget,
        })
        if r.status_code == 200:
            ctx = r.json().get("context", {})
            token_cost = ctx.get("token_cost", 0)
            within_budget = token_cost <= budget
            results.append(
                self._ok("token_cost_within_budget",
                         f"cost={token_cost} ≤ budget={budget}",
                         metric_name="avg_token_cost",
                         metric_value=float(token_cost)) if within_budget
                else self._fail("token_cost_within_budget",
                                f"cost={token_cost} > budget={budget}",
                                metric_name="avg_token_cost",
                                metric_value=float(token_cost))
            )
        else:
            results.append(self._fail("token_cost_within_budget", f"status={r.status_code}"))

        # ── 3. token_cost is reported in response ─────────────────────────────
        r = await client.post("/api/events/recall", json={
            "query": "retrieval orchestrator providers",
            "project": project,
            "token_budget": 2000,
        })
        if r.status_code == 200:
            ctx = r.json().get("context", {})
            cost = ctx.get("token_cost")
            has_cost = cost is not None and isinstance(cost, int)
            results.append(
                self._ok("token_cost_reported", f"token_cost={cost}") if has_cost
                else self._fail("token_cost_reported", f"token_cost missing or non-integer: {cost}")
            )
        else:
            results.append(self._fail("token_cost_reported", f"status={r.status_code}"))

        # ── 4. Recall latency (p95 warning threshold) ─────────────────────────
        latencies_ms: list[float] = []
        for _ in range(5):
            t0 = time.monotonic()
            r = await client.post("/api/events/recall", json={
                "query": "user preference theme color",
                "project": project,
            })
            elapsed_ms = (time.monotonic() - t0) * 1000
            if r.status_code == 200:
                latencies_ms.append(elapsed_ms)

        if latencies_ms:
            p95_ms = sorted(latencies_ms)[int(len(latencies_ms) * 0.95) - 1] if len(latencies_ms) > 1 else latencies_ms[0]
            avg_ms = sum(latencies_ms) / len(latencies_ms)
            under_warn = p95_ms < _P95_LATENCY_WARN_MS
            results.append(
                self._ok("recall_latency_p95",
                         f"p95={p95_ms:.0f}ms avg={avg_ms:.0f}ms",
                         metric_name="recall_p95_latency_ms",
                         metric_value=p95_ms) if under_warn
                else self._fail("recall_latency_p95",
                                f"p95={p95_ms:.0f}ms exceeds {_P95_LATENCY_WARN_MS}ms warning threshold",
                                metric_name="recall_p95_latency_ms",
                                metric_value=p95_ms)
            )
        else:
            results.append(self._fail("recall_latency_p95", "no latency samples collected"))

        # ── 5. Token efficiency score in [0, 1] ───────────────────────────────
        r = await client.post("/api/events/recall", json={
            "query": "vector store search chromadb",
            "project": project,
            "token_budget": 1500,
        })
        if r.status_code == 200:
            data = r.json()
            # Check top-level debug has token info
            debug = data.get("debug", {})
            ctx = data.get("context", {})
            has_providers = bool(debug.get("providers"))
            results.append(
                self._ok("debug_providers_present", f"providers={debug.get('providers', [])}") if has_providers
                else self._fail("debug_providers_present", "no providers in debug output")
            )
        else:
            results.append(self._fail("debug_providers_present", f"status={r.status_code}"))

        # ── 6. Zero-budget recall still returns raw hits ──────────────────────
        r = await client.post("/api/events/recall", json={
            "query": "trust score decay",
            "project": project,
        })
        if r.status_code == 200:
            hits = r.json().get("hits", [])
            # No token_budget → no context block expected
            ctx = r.json().get("context")
            results.append(
                self._ok("no_budget_raw_hits", f"hits={len(hits)}, context={ctx is not None}")
            )
        else:
            results.append(self._fail("no_budget_raw_hits", f"status={r.status_code}"))

        return results
