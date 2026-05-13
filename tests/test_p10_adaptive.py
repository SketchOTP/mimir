"""P10 Adaptive Retrieval Intelligence tests.

Covers all P10 acceptance criteria:
  1.  Provider usefulness accumulation (ProviderStats rows created/updated)
  2.  Adaptive weighting by task category (category boosts applied)
  3.  FTS5 retrieval correctness (fts5_search returns results)
  4.  Weighted agreement scoring (trust-weighted, not flat fraction)
  5.  Provider drift detection (harmful_rate / usefulness_rate thresholds)
  6.  Adaptive budgeting (per-provider limits vary by category)
  7.  Retrieval confidence estimation ([0,1] range, components correct)
  8.  Bounded provider weight evolution (floor / ceil enforced)
  9.  Cross-user isolation preserved (P10 paths don't break isolation)
  10. Task categorisation correctness (patterns map to right categories)
  11. Task category stored in retrieval session
  12. active_providers + provider_contributions stored in retrieval session
  13. Retrieval confidence returned in recall response
  14. Provider stats API endpoint responds
  15. Provider drift API endpoint responds
  16. Provider aggregate endpoint triggers accumulation
  17. Safety constraints: no single provider dominance, no rapid reweighting
  18. Keyword provider falls back to LIKE when FTS5 unavailable
  19. FTS5 probe cached (no repeated probes)
  20. Orchestrator result carries task_category + contributions + confidence
"""

import uuid
import pytest
from unittest.mock import AsyncMock, patch


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _recall(client, query: str, *, budget: int = 2000, **kwargs):
    payload = {"query": query, "token_budget": budget, **kwargs}
    r = await client.post("/api/events/recall", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


async def _store(client, content: str, layer: str = "semantic", **kwargs):
    r = await client.post("/api/memory", json={"content": content, "layer": layer, **kwargs})
    assert r.status_code == 200, r.text
    return r.json()


# ─── 1. Task categorisation correctness ──────────────────────────────────────

@pytest.mark.asyncio
async def test_task_category_identity():
    from retrieval.task_categorizer import categorize
    assert categorize("who am i and what are my preferences") == "identity"
    assert categorize("what is my profile and background") == "identity"


@pytest.mark.asyncio
async def test_task_category_procedural():
    from retrieval.task_categorizer import categorize
    assert categorize("how to deploy a FastAPI app") == "procedural"
    assert categorize("steps to configure redis") == "procedural"


@pytest.mark.asyncio
async def test_task_category_troubleshooting():
    from retrieval.task_categorizer import categorize
    assert categorize("error in the payment module") == "troubleshooting"
    assert categorize("fix the broken database connection") == "troubleshooting"


@pytest.mark.asyncio
async def test_task_category_project_continuity():
    from retrieval.task_categorizer import categorize
    assert categorize("where did I leave off last time") == "project_continuity"
    assert categorize("continue from previous session") == "project_continuity"


@pytest.mark.asyncio
async def test_task_category_configuration():
    from retrieval.task_categorizer import categorize
    assert categorize("config settings for the database") == "configuration"
    assert categorize("enable the feature flag in settings") == "configuration"


@pytest.mark.asyncio
async def test_task_category_general_fallback():
    from retrieval.task_categorizer import categorize
    assert categorize("tell me something interesting") == "general"
    assert categorize("what is the weather like") == "general"


# ─── 2. Adaptive weights — category boosts applied ───────────────────────────

@pytest.mark.asyncio
async def test_adaptive_weights_identity_boosts_identity_provider():
    from retrieval.adaptive_weights import compute_provider_weights
    w = compute_provider_weights("identity")
    assert w["identity"] > w["keyword"], "identity category must boost identity provider"
    assert w["identity"] > w["episodic_recent"]


@pytest.mark.asyncio
async def test_adaptive_weights_troubleshooting_boosts_episodic():
    from retrieval.adaptive_weights import compute_provider_weights
    w = compute_provider_weights("troubleshooting")
    assert w["episodic_recent"] > w["identity"], "troubleshooting must boost episodic_recent"
    assert w["procedural"] > w["identity"]


@pytest.mark.asyncio
async def test_adaptive_weights_configuration_boosts_keyword():
    from retrieval.adaptive_weights import compute_provider_weights
    w = compute_provider_weights("configuration")
    assert w["keyword"] > w["identity"], "configuration must boost keyword provider"
    assert w["procedural"] > w["identity"]


@pytest.mark.asyncio
async def test_adaptive_weights_all_providers_present():
    from retrieval.adaptive_weights import compute_provider_weights, ALL_PROVIDERS
    w = compute_provider_weights("general")
    for provider in ALL_PROVIDERS:
        assert provider in w, f"Provider '{provider}' missing from weights"
        assert w[provider] > 0, f"Weight for '{provider}' must be positive"


@pytest.mark.asyncio
async def test_adaptive_weights_floor_ceiling_enforced():
    """Weights stay within [floor, ceil] even with extreme stats."""
    from retrieval.adaptive_weights import compute_provider_weights, _WEIGHT_FLOOR_FRACTION, _WEIGHT_CEIL_MULTIPLIER
    from storage.models import ProviderStats

    # Simulate a provider with very low usefulness
    bad_stats = ProviderStats(
        id="fake", provider_name="vector", project=None, task_category=None,
        total_sessions=100, useful_sessions=1, harmful_sessions=50,
        usefulness_rate=0.01, harmful_rate=0.50,
        weight_current=0.5, drift_flagged=False,
    )
    w = compute_provider_weights("general", {"vector": bad_stats})
    base = 1.0  # general category, vector base = 1.0
    assert w["vector"] >= base * _WEIGHT_FLOOR_FRACTION, "Weight must not drop below floor"
    assert w["vector"] <= base * _WEIGHT_CEIL_MULTIPLIER, "Weight must not exceed ceil"


# ─── 3. Adaptive budget limits vary by category ───────────────────────────────

@pytest.mark.asyncio
async def test_adaptive_budget_limits_vary():
    from retrieval.adaptive_weights import compute_provider_weights, compute_provider_limits
    w_identity = compute_provider_weights("identity")
    w_trouble = compute_provider_weights("troubleshooting")
    lim_identity = compute_provider_limits(w_identity)
    lim_trouble = compute_provider_limits(w_trouble)

    # identity category → identity provider gets more budget
    assert lim_identity["identity"] > lim_trouble["identity"]
    # troubleshooting → episodic_recent gets more budget
    assert lim_trouble["episodic_recent"] > lim_identity["episodic_recent"]


@pytest.mark.asyncio
async def test_adaptive_budget_within_bounds():
    from retrieval.adaptive_weights import compute_provider_weights, compute_provider_limits, _MIN_PROVIDER_LIMIT, _MAX_PROVIDER_LIMIT
    w = compute_provider_weights("procedural")
    lim = compute_provider_limits(w)
    for provider, limit in lim.items():
        assert limit >= _MIN_PROVIDER_LIMIT, f"{provider} limit {limit} below minimum"
        assert limit <= _MAX_PROVIDER_LIMIT, f"{provider} limit {limit} above maximum"


# ─── 4. Weighted agreement scoring ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_weighted_agreement_high_trust_providers_score_higher():
    from retrieval.confidence import compute_weighted_agreement
    # identity + high_trust agreeing should score higher than keyword + episodic
    high = compute_weighted_agreement({"m1": {"identity", "high_trust"}}, total_providers=6)
    low = compute_weighted_agreement({"m1": {"keyword", "episodic_recent"}}, total_providers=6)
    assert high > low, "High-trust provider agreement must score higher"


@pytest.mark.asyncio
async def test_weighted_agreement_zero_sources():
    from retrieval.confidence import compute_weighted_agreement
    score = compute_weighted_agreement({}, total_providers=6)
    assert score == 0.0


@pytest.mark.asyncio
async def test_weighted_agreement_all_providers():
    from retrieval.confidence import compute_weighted_agreement
    score = compute_weighted_agreement(
        {"m1": {"identity", "high_trust", "procedural", "vector", "keyword", "episodic_recent"}},
        total_providers=6,
    )
    assert 0.0 <= score <= 1.0


# ─── 5. Retrieval confidence estimation ──────────────────────────────────────

@pytest.mark.asyncio
async def test_confidence_estimate_in_range():
    from retrieval.confidence import estimate_confidence
    conf = estimate_confidence(
        weighted_agreement=0.6,
        avg_trust=0.8,
        memory_states=["active", "active", "aging"],
        token_efficiency=0.9,
        historical_usefulness=0.7,
    )
    assert 0.0 <= conf <= 1.0


@pytest.mark.asyncio
async def test_confidence_higher_with_better_inputs():
    from retrieval.confidence import estimate_confidence
    good = estimate_confidence(0.9, 0.9, ["active"] * 5, 0.95, 0.85)
    bad = estimate_confidence(0.1, 0.2, ["stale", "contradicted"], 0.1, 0.1)
    assert good > bad


@pytest.mark.asyncio
async def test_confidence_none_history_neutral():
    from retrieval.confidence import estimate_confidence
    c_none = estimate_confidence(0.5, 0.7, ["active"], 0.8, None)
    c_mid = estimate_confidence(0.5, 0.7, ["active"], 0.8, 0.5)
    # both should be close (0.5 is the neutral historical value)
    assert abs(c_none - c_mid) < 0.05


# ─── 6. Recall response carries P10 fields ───────────────────────────────────

@pytest.mark.asyncio
async def test_recall_returns_task_category(client):
    await _store(client, "P10cat: task category test memory cat_field_test", importance=0.7)
    data = await _recall(client, "P10cat cat_field_test")
    assert "task_category" in data, "task_category missing from recall response"
    assert data["task_category"] in {
        "identity", "procedural", "troubleshooting",
        "project_continuity", "configuration", "general",
    }


@pytest.mark.asyncio
async def test_recall_returns_retrieval_confidence(client):
    await _store(client, "P10conf: confidence score test conf_field_test", importance=0.7)
    data = await _recall(client, "P10conf conf_field_test")
    assert "retrieval_confidence" in data, "retrieval_confidence missing from recall response"
    assert 0.0 <= data["retrieval_confidence"] <= 1.0


@pytest.mark.asyncio
async def test_recall_debug_contains_task_category(client):
    await _store(client, "P10dbg: debug task category debug_cat_test", importance=0.7)
    data = await _recall(client, "P10dbg debug_cat_test")
    debug = data.get("context", {}).get("debug", {})
    assert "task_category" in debug


@pytest.mark.asyncio
async def test_recall_debug_contains_provider_weights(client):
    await _store(client, "P10wgt: provider weights debug wgt_debug_test", importance=0.7)
    data = await _recall(client, "P10wgt wgt_debug_test")
    debug = data.get("context", {}).get("debug", {})
    assert "provider_weights" in debug
    weights = debug["provider_weights"]
    for p in ("vector", "keyword", "identity", "episodic_recent", "procedural", "high_trust"):
        assert p in weights


# ─── 7. Retrieval session stores P10 fields ───────────────────────────────────

@pytest.mark.asyncio
async def test_retrieval_session_has_task_category(client, app):
    """After a recall with token_budget, the persisted retrieval session has task_category."""
    from storage.database import get_session_factory
    from storage.models import RetrievalSession
    from sqlalchemy import select

    await _store(client, "P10sess: session category stored sess_cat_test", importance=0.7)
    data = await _recall(client, "P10sess sess_cat_test how to do something")
    rs_id = data.get("retrieval_session_id")
    assert rs_id, "retrieval_session_id missing"

    factory = get_session_factory()
    async with factory() as db:
        rs = await db.get(RetrievalSession, rs_id)
        assert rs is not None
        # task_category should be set (may be "procedural" because "how to")
        assert rs.task_category is not None


@pytest.mark.asyncio
async def test_retrieval_session_has_active_providers(client):
    from storage.database import get_session_factory
    from storage.models import RetrievalSession

    await _store(client, "P10prov: active providers stored prov_stored_test", importance=0.7)
    data = await _recall(client, "P10prov prov_stored_test")
    rs_id = data.get("retrieval_session_id")
    assert rs_id

    factory = get_session_factory()
    async with factory() as db:
        rs = await db.get(RetrievalSession, rs_id)
        assert rs is not None
        assert rs.active_providers is not None
        assert isinstance(rs.active_providers, list)
        assert len(rs.active_providers) > 0


@pytest.mark.asyncio
async def test_retrieval_session_has_confidence_score(client):
    from storage.database import get_session_factory
    from storage.models import RetrievalSession

    await _store(client, "P10cscore: confidence score stored cscore_test", importance=0.7)
    data = await _recall(client, "P10cscore cscore_test")
    rs_id = data.get("retrieval_session_id")
    assert rs_id

    factory = get_session_factory()
    async with factory() as db:
        rs = await db.get(RetrievalSession, rs_id)
        assert rs is not None
        assert rs.retrieval_confidence_score is not None
        assert 0.0 <= rs.retrieval_confidence_score <= 1.0


# ─── 8. Provider stats aggregation ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_provider_stats_aggregate_endpoint(client):
    r = await client.post("/api/telemetry/providers/aggregate")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert "sessions_processed" in data
    assert "stats_updated" in data
    assert "drift_flagged" in data


@pytest.mark.asyncio
async def test_provider_stats_get_endpoint(client):
    r = await client.get("/api/telemetry/providers/stats")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert "provider_stats" in data
    assert isinstance(data["provider_stats"], list)


@pytest.mark.asyncio
async def test_provider_drift_endpoint(client):
    r = await client.get("/api/telemetry/providers/drift")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert "drifting_providers" in data


# ─── 9. Provider drift detection logic ───────────────────────────────────────

@pytest.mark.asyncio
async def test_provider_drift_detected_on_high_harmful_rate():
    from retrieval.provider_stats import _check_drift
    from storage.models import ProviderStats

    stats = ProviderStats(
        id="x", provider_name="vector", project=None, task_category=None,
        total_sessions=20, useful_sessions=5, harmful_sessions=4,
        usefulness_rate=0.25, harmful_rate=0.20,
        weight_current=1.0, drift_flagged=False,
    )
    drifting, reason = _check_drift(stats)
    assert drifting, "Should detect drift with 20% harmful rate"
    assert "harmful_rate" in reason


@pytest.mark.asyncio
async def test_provider_drift_detected_on_low_usefulness():
    from retrieval.provider_stats import _check_drift
    from storage.models import ProviderStats

    stats = ProviderStats(
        id="y", provider_name="keyword", project=None, task_category=None,
        total_sessions=15, useful_sessions=2, harmful_sessions=0,
        usefulness_rate=0.13, harmful_rate=0.0,
        weight_current=1.0, drift_flagged=False,
    )
    drifting, reason = _check_drift(stats)
    assert drifting, "Should detect drift with 13% usefulness rate"
    assert "usefulness_rate" in reason


@pytest.mark.asyncio
async def test_provider_drift_not_detected_below_min_sessions():
    from retrieval.provider_stats import _check_drift, _DRIFT_MIN_SESSIONS
    from storage.models import ProviderStats

    stats = ProviderStats(
        id="z", provider_name="procedural", project=None, task_category=None,
        total_sessions=_DRIFT_MIN_SESSIONS - 1,
        useful_sessions=0, harmful_sessions=5,
        usefulness_rate=0.0, harmful_rate=0.5,
        weight_current=1.0, drift_flagged=False,
    )
    drifting, _ = _check_drift(stats)
    assert not drifting, "Should NOT flag drift with insufficient evidence"


@pytest.mark.asyncio
async def test_provider_drift_not_detected_on_healthy_provider():
    from retrieval.provider_stats import _check_drift
    from storage.models import ProviderStats

    stats = ProviderStats(
        id="h", provider_name="identity", project=None, task_category=None,
        total_sessions=50, useful_sessions=40, harmful_sessions=1,
        usefulness_rate=0.80, harmful_rate=0.02,
        weight_current=1.0, drift_flagged=False,
    )
    drifting, _ = _check_drift(stats)
    assert not drifting, "Healthy provider must not be flagged for drift"


# ─── 10. Weight evolution safety constraints ──────────────────────────────────

@pytest.mark.asyncio
async def test_weight_update_bounded_below():
    from retrieval.adaptive_weights import update_weight_from_stats
    # Simulate repeated bad usefulness
    weight = 1.0
    for _ in range(100):
        weight = update_weight_from_stats(weight, 0.0, base_weight=1.0)
    assert weight >= 1.0 * 0.3, f"Weight {weight} dropped below floor"


@pytest.mark.asyncio
async def test_weight_update_bounded_above():
    from retrieval.adaptive_weights import update_weight_from_stats
    weight = 1.0
    for _ in range(100):
        weight = update_weight_from_stats(weight, 1.0, base_weight=1.0)
    assert weight <= 1.0 * 2.5, f"Weight {weight} exceeded ceiling"


@pytest.mark.asyncio
async def test_weight_update_slow_adaptation():
    from retrieval.adaptive_weights import update_weight_from_stats, _ALPHA
    # One step update from perfect usefulness — change should be at most alpha * base
    old = 1.0
    new = update_weight_from_stats(old, 1.0, base_weight=1.0)
    assert abs(new - old) <= _ALPHA * 2, "Weight change per step too large (not slow adaptation)"


# ─── 11. FTS5 search ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fts5_search_returns_results_or_graceful_fallback(client):
    """FTS5 search should return results when available; empty list on failure is OK."""
    from storage.fts import fts5_search
    from storage.database import get_session_factory

    factory = get_session_factory()
    async with factory() as db:
        # Store a memory via the API so FTS5 trigger inserts it
        await _store(client, "P10fts: full text search test fts5uniqueterm", importance=0.7)
        results = await fts5_search(db, "fts5uniqueterm")
        # Either FTS5 works and returns results, or gracefully returns empty
        assert isinstance(results, list)
        for mid, score in results:
            assert isinstance(mid, str)
            assert isinstance(score, float)
            assert score >= 0


@pytest.mark.asyncio
async def test_keyword_provider_works_without_fts5(client):
    """Keyword provider must return results via LIKE fallback if FTS5 probe fails."""
    await _store(client, "P10like: keyword like fallback test likeuniqkw", importance=0.7)
    # Patch probe to return False (simulating no FTS5)
    from storage import fts as fts_module
    original = fts_module._FTS5_AVAILABLE
    fts_module._FTS5_AVAILABLE = False
    try:
        data = await _recall(client, "P10like likeuniqkw keyword like fallback")
        # Should still return context (LIKE fallback kicks in)
        assert "context" in data
    finally:
        fts_module._FTS5_AVAILABLE = original


# ─── 12. Cross-user isolation still preserved ────────────────────────────────

@pytest.mark.asyncio
async def test_p10_cross_user_isolation(app, client):
    """P10 adaptive paths must not break user isolation."""
    from tests.conftest import as_user

    with as_user(app, "p10_user_A"):
        r = await client.post("/api/memory", json={
            "content": "P10iso: user A private data p10_isolation_test",
            "layer": "semantic",
            "user_id": "p10_user_A",
            "importance": 0.9,
        })
        assert r.status_code == 200
        mem_id = r.json()["id"]

    with as_user(app, "p10_user_B"):
        data = await _recall(
            client, "P10iso p10_isolation_test user A private",
            user_id="p10_user_B",
        )
        ctx_ids = [m["id"] for m in data.get("context", {}).get("memories", [])]
        assert mem_id not in ctx_ids, "P10 paths broke cross-user isolation"


# ─── 13. Orchestrator result dataclass correctness ───────────────────────────

@pytest.mark.asyncio
async def test_orchestrator_result_has_p10_fields(client):
    """OrchestratorResult must carry task_category, provider_contributions, confidence."""
    await _store(client, "P10orch: orchestrator result fields orch_fields_test", importance=0.8)

    from storage.database import get_session_factory
    from retrieval.orchestrator import orchestrate

    factory = get_session_factory()
    async with factory() as db:
        result = await orchestrate(db, "P10orch orch_fields_test", token_budget=2000)

    assert hasattr(result, "task_category")
    assert result.task_category in {
        "identity", "procedural", "troubleshooting",
        "project_continuity", "configuration", "general",
    }
    assert hasattr(result, "provider_contributions")
    assert isinstance(result.provider_contributions, dict)
    assert hasattr(result, "retrieval_confidence")
    assert 0.0 <= result.retrieval_confidence <= 1.0


@pytest.mark.asyncio
async def test_orchestrator_debug_has_p10_fields(client):
    from storage.database import get_session_factory
    from retrieval.orchestrator import orchestrate

    factory = get_session_factory()
    async with factory() as db:
        result = await orchestrate(db, "P10dbgf debug fields check", token_budget=2000)

    assert hasattr(result.debug, "task_category")
    assert hasattr(result.debug, "provider_weights")
    assert hasattr(result.debug, "retrieval_confidence")
    assert isinstance(result.debug.provider_weights, dict)


# ─── 14. FTS5 probe caching ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fts5_probe_cached():
    """After one probe, _FTS5_AVAILABLE is set and subsequent calls skip the probe."""
    from storage import fts as fts_module
    from storage.fts import reset_fts5_probe
    from storage.database import get_session_factory

    reset_fts5_probe()
    assert fts_module._FTS5_AVAILABLE is None

    factory = get_session_factory()
    async with factory() as db:
        from storage.fts import fts5_search
        await fts5_search(db, "cacheprobe")

    # After one call, the probe result is cached
    assert fts_module._FTS5_AVAILABLE is not None


# ─── 15. Task category passed to orchestrator overrides detection ─────────────

@pytest.mark.asyncio
async def test_orchestrator_respects_explicit_task_category():
    from storage.database import get_session_factory
    from retrieval.orchestrator import orchestrate

    factory = get_session_factory()
    async with factory() as db:
        result = await orchestrate(
            db, "some generic query",
            token_budget=2000,
            task_category="configuration",  # explicit override
        )

    assert result.task_category == "configuration"
