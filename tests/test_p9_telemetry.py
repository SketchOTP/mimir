"""P9 Autonomous Feedback + Cognitive Telemetry tests.

Covers all P9 acceptance criteria:
  - Automatic positive retrieval inference
  - Automatic harmful/negative retrieval inference
  - Retrieval session tracking (creation, outcome recording)
  - Retrieval quality scores (relevance, usefulness, harmfulness, agreement, token_efficiency)
  - Procedural effectiveness scoring
  - Confidence drift detection
  - Trust decay from drift
  - Token efficiency analytics
  - Rollback correlation
  - Retrieval usefulness metrics
  - Telemetry snapshot computation and persistence
  - Metric history retrieval
  - Telemetry API endpoints (functional)
  - UI endpoint registration
  - Safety constraints (quarantined not affected, bounded deltas)
"""

from __future__ import annotations

import uuid
from datetime import datetime, UTC

import pytest
from sqlalchemy import select

from storage.models import (
    Memory, RetrievalSession, TelemetrySnapshot, RetrievalFeedback, LifecycleEvent,
)
from memory.trust import MemoryState


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _id() -> str:
    return uuid.uuid4().hex


async def _make_memory(
    session,
    *,
    layer="procedural",
    content="test memory",
    project=None,
    memory_state="active",
    trust_score=0.7,
    times_retrieved=0,
    successful_retrievals=0,
    failed_retrievals=0,
):
    mem = Memory(
        id=_id(),
        layer=layer,
        content=content,
        project=project,
        memory_state=memory_state,
        trust_score=trust_score,
        importance=0.7,
        times_retrieved=times_retrieved,
        successful_retrievals=successful_retrievals,
        failed_retrievals=failed_retrievals,
    )
    session.add(mem)
    await session.commit()
    return mem


async def _make_retrieval_session(
    session,
    *,
    memory_ids: list[str],
    task_outcome: str | None = None,
    has_correction: bool = False,
    has_harmful_outcome: bool = False,
    rollback_id: str | None = None,
    token_cost: int = 1000,
    agreement_score: float | None = 0.5,
    project: str | None = None,
) -> RetrievalSession:
    rs = RetrievalSession(
        id=_id(),
        query="test query",
        project=project,
        retrieved_memory_ids=memory_ids,
        result_count=len(memory_ids),
        token_cost=token_cost,
        task_outcome=task_outcome,
        has_correction=has_correction,
        has_harmful_outcome=has_harmful_outcome,
        rollback_id=rollback_id,
        agreement_score=agreement_score,
        inference_applied=False,
    )
    session.add(rs)
    await session.commit()
    return rs


# ─── ORM: RetrievalSession schema ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retrieval_session_orm(app):
    """RetrievalSession can be created and queried."""
    from storage.database import get_session_factory
    factory = get_session_factory()
    async with factory() as session:
        rs = RetrievalSession(
            id=_id(),
            query="what is X",
            session_id="sess-1",
            project="proj-orm",
            retrieved_memory_ids=["m1", "m2"],
            result_count=2,
            token_cost=512,
        )
        session.add(rs)
        await session.commit()

        loaded = await session.get(RetrievalSession, rs.id)
        assert loaded is not None
        assert loaded.query == "what is X"
        assert loaded.result_count == 2
        assert "m1" in loaded.retrieved_memory_ids
        assert loaded.inference_applied is False


@pytest.mark.asyncio
async def test_telemetry_snapshot_orm(app):
    """TelemetrySnapshot can be created and queried."""
    from storage.database import get_session_factory
    factory = get_session_factory()
    async with factory() as session:
        snap = TelemetrySnapshot(
            id=_id(),
            metric_name="retrieval_usefulness_rate",
            metric_value=0.75,
            period="daily",
            project="proj-snap",
        )
        session.add(snap)
        await session.commit()

        loaded = await session.get(TelemetrySnapshot, snap.id)
        assert loaded is not None
        assert loaded.metric_name == "retrieval_usefulness_rate"
        assert abs(loaded.metric_value - 0.75) < 0.001


# ─── Automatic Feedback Inference ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_positive_inference_on_success(app):
    """Successful session with no rollback/correction → positive trust nudge."""
    from storage.database import get_session_factory
    from worker.feedback_inference import infer_retrieval_outcomes

    factory = get_session_factory()
    async with factory() as session:
        proj = f"infer-pos-{_id()[:8]}"
        mem = await _make_memory(session, project=proj, trust_score=0.70)
        rs = await _make_retrieval_session(
            session,
            memory_ids=[mem.id],
            task_outcome="success",
            has_correction=False,
            has_harmful_outcome=False,
            project=proj,
        )

        result = await infer_retrieval_outcomes(session)
        assert result["positive_inferred"] >= 1

        await session.refresh(mem)
        assert mem.trust_score > 0.70  # trust nudged up
        assert mem.trust_score <= 0.99  # bounded


@pytest.mark.asyncio
async def test_negative_inference_on_rollback(app):
    """Session with rollback_id → negative trust inference."""
    from storage.database import get_session_factory
    from worker.feedback_inference import infer_retrieval_outcomes

    factory = get_session_factory()
    async with factory() as session:
        proj = f"infer-neg-{_id()[:8]}"
        mem = await _make_memory(session, project=proj, trust_score=0.75)
        await _make_retrieval_session(
            session,
            memory_ids=[mem.id],
            task_outcome="failure",
            rollback_id="rollback-xyz",
            project=proj,
        )

        result = await infer_retrieval_outcomes(session)
        assert result["negative_inferred"] >= 1

        await session.refresh(mem)
        assert mem.trust_score < 0.75  # trust nudged down
        assert mem.trust_score >= 0.01  # bounded


@pytest.mark.asyncio
async def test_negative_inference_on_harmful(app):
    """Session with has_harmful_outcome → negative inference."""
    from storage.database import get_session_factory
    from worker.feedback_inference import infer_retrieval_outcomes

    factory = get_session_factory()
    async with factory() as session:
        proj = f"infer-harm-{_id()[:8]}"
        mem = await _make_memory(session, project=proj, trust_score=0.72)
        await _make_retrieval_session(
            session,
            memory_ids=[mem.id],
            task_outcome="success",  # outcome "success" but harmful flag set
            has_harmful_outcome=True,
            project=proj,
        )

        result = await infer_retrieval_outcomes(session)
        assert result["negative_inferred"] >= 1

        await session.refresh(mem)
        assert mem.trust_score < 0.72


@pytest.mark.asyncio
async def test_negative_inference_on_correction(app):
    """Session with has_correction → negative inference."""
    from storage.database import get_session_factory
    from worker.feedback_inference import infer_retrieval_outcomes

    factory = get_session_factory()
    async with factory() as session:
        proj = f"infer-corr-{_id()[:8]}"
        mem = await _make_memory(session, project=proj, trust_score=0.68)
        await _make_retrieval_session(
            session,
            memory_ids=[mem.id],
            task_outcome="failure",
            has_correction=True,
            project=proj,
        )

        result = await infer_retrieval_outcomes(session)
        assert result["negative_inferred"] >= 1

        await session.refresh(mem)
        assert mem.trust_score < 0.68


@pytest.mark.asyncio
async def test_inference_idempotent(app):
    """Running inference twice on the same session does not double-apply."""
    from storage.database import get_session_factory
    from worker.feedback_inference import infer_retrieval_outcomes

    factory = get_session_factory()
    async with factory() as session:
        proj = f"infer-idem-{_id()[:8]}"
        mem = await _make_memory(session, project=proj, trust_score=0.70)
        await _make_retrieval_session(
            session,
            memory_ids=[mem.id],
            task_outcome="success",
            project=proj,
        )

        await infer_retrieval_outcomes(session)
        await session.refresh(mem)
        trust_after_first = mem.trust_score

        # Second run: session is now marked inference_applied=True
        result2 = await infer_retrieval_outcomes(session)
        assert result2["sessions_processed"] == 0  # no new pending sessions

        await session.refresh(mem)
        assert abs(mem.trust_score - trust_after_first) < 0.001


@pytest.mark.asyncio
async def test_inference_skips_quarantined(app):
    """Quarantined memories are never touched by inference."""
    from storage.database import get_session_factory
    from worker.feedback_inference import infer_retrieval_outcomes

    factory = get_session_factory()
    async with factory() as session:
        proj = f"infer-quar-{_id()[:8]}"
        mem = await _make_memory(
            session,
            project=proj,
            memory_state=MemoryState.QUARANTINED,
            trust_score=0.2,
        )
        await _make_retrieval_session(
            session,
            memory_ids=[mem.id],
            task_outcome="success",
            project=proj,
        )

        await infer_retrieval_outcomes(session)
        await session.refresh(mem)
        assert abs(mem.trust_score - 0.2) < 0.001  # unchanged


@pytest.mark.asyncio
async def test_inference_bounded_delta(app):
    """Inferred trust delta is at most +0.01 per session."""
    from storage.database import get_session_factory
    from worker.feedback_inference import infer_retrieval_outcomes

    factory = get_session_factory()
    async with factory() as session:
        proj = f"infer-bound-{_id()[:8]}"
        mem = await _make_memory(session, project=proj, trust_score=0.70)
        await _make_retrieval_session(
            session, memory_ids=[mem.id], task_outcome="success", project=proj
        )

        await infer_retrieval_outcomes(session)
        await session.refresh(mem)
        delta = mem.trust_score - 0.70
        assert delta <= 0.011  # max +0.01 per session
        assert delta >= 0.005  # some nudge applied


# ─── Retrieval Session Tracking (API) ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_recall_creates_retrieval_session(client, app):
    """POST /api/events/recall with token_budget creates a RetrievalSession."""
    from tests.conftest import as_user
    with as_user(app, "user-rs-1"):
        r = await client.post("/api/events/recall", json={
            "query": "session tracking test",
            "token_budget": 1000,
            "project": f"rs-test-{_id()[:6]}",
        })
    assert r.status_code == 200
    data = r.json()
    assert "retrieval_session_id" in data
    assert data["retrieval_session_id"]  # non-empty


@pytest.mark.asyncio
async def test_record_session_outcome_success(client, app):
    """POST /api/events/recall/session/{id}/outcome records success outcome."""
    from tests.conftest import as_user

    # First create a retrieval session
    proj = f"out-test-{_id()[:6]}"
    with as_user(app, "user-out-1"):
        r = await client.post("/api/events/recall", json={
            "query": "outcome test",
            "token_budget": 500,
            "project": proj,
        })
    rs_id = r.json().get("retrieval_session_id")
    assert rs_id

    # Record outcome
    with as_user(app, "user-out-1"):
        r2 = await client.post(f"/api/events/recall/session/{rs_id}/outcome", json={
            "task_outcome": "success",
        })
    assert r2.status_code == 200
    data = r2.json()
    assert data["ok"] is True
    assert data["task_outcome"] == "success"
    assert "quality_scores" in data


@pytest.mark.asyncio
async def test_record_session_outcome_failure(client, app):
    """POST session outcome with failure + rollback_id persists correctly."""
    from tests.conftest import as_user

    proj = f"out-fail-{_id()[:6]}"
    with as_user(app, "user-out-2"):
        r = await client.post("/api/events/recall", json={
            "query": "failure outcome test",
            "token_budget": 500,
            "project": proj,
        })
    rs_id = r.json().get("retrieval_session_id")

    with as_user(app, "user-out-2"):
        r2 = await client.post(f"/api/events/recall/session/{rs_id}/outcome", json={
            "task_outcome": "failure",
            "rollback_id": "rb-123",
            "has_correction": True,
        })
    assert r2.status_code == 200
    assert r2.json()["quality_scores"]["harmfulness_score"] == 0.0  # no harmful flag
    assert r2.json()["quality_scores"]["usefulness_score"] < 0.5  # low on failure


@pytest.mark.asyncio
async def test_record_session_outcome_invalid(client, app):
    """Invalid task_outcome returns 422."""
    from tests.conftest import as_user

    with as_user(app, "user-inv-1"):
        r = await client.post("/api/events/recall/session/fake-id/outcome", json={
            "task_outcome": "explode",
        })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_record_session_outcome_not_found(client, app):
    """Non-existent session returns 404."""
    from tests.conftest import as_user

    with as_user(app, "user-inv-2"):
        r = await client.post("/api/events/recall/session/nonexistent/outcome", json={
            "task_outcome": "success",
        })
    assert r.status_code == 404


# ─── Retrieval Quality Scores ─────────────────────────────────────────────────

def test_quality_scores_success_outcome():
    """compute_session_quality_scores: success outcome → high usefulness."""
    from telemetry.retrieval_analytics import compute_session_quality_scores
    scores = compute_session_quality_scores(
        ["m1", "m2"], 800, 1000, {"m1": 0.5, "m2": 0.8},
        task_outcome="success"
    )
    assert scores["usefulness_score"] > 0.5
    assert scores["harmfulness_score"] == 0.0
    assert 0.0 <= scores["token_efficiency_score"] <= 1.0
    assert 0.0 <= scores["relevance_score"] <= 1.0


def test_quality_scores_failure_outcome():
    """compute_session_quality_scores: failure outcome → low usefulness."""
    from telemetry.retrieval_analytics import compute_session_quality_scores
    scores = compute_session_quality_scores(
        ["m1"], 500, 1000, {"m1": 0.3},
        task_outcome="failure"
    )
    assert scores["usefulness_score"] < 0.5


def test_quality_scores_harmful():
    """compute_session_quality_scores: harmful flag → harmfulness_score == 1.0."""
    from telemetry.retrieval_analytics import compute_session_quality_scores
    scores = compute_session_quality_scores(
        ["m1"], 500, 1000, {"m1": 0.4},
        has_harmful_outcome=True
    )
    assert scores["harmfulness_score"] == 1.0


def test_quality_scores_empty():
    """Empty memory list → all zeros."""
    from telemetry.retrieval_analytics import compute_session_quality_scores
    scores = compute_session_quality_scores([], 0, 1000, {})
    assert all(v == 0.0 for v in scores.values())


def test_token_efficiency_calculation():
    """Token efficiency is max at budget utilisation ~1.0."""
    from telemetry.retrieval_analytics import compute_session_quality_scores
    # Perfect utilisation
    s_perfect = compute_session_quality_scores(["m"], 1000, 1000, {"m": 0.5})
    # Extreme under-utilisation
    s_low = compute_session_quality_scores(["m"], 100, 1000, {"m": 0.5})
    assert s_perfect["token_efficiency_score"] > s_low["token_efficiency_score"]


# ─── Procedural Effectiveness Analytics ───────────────────────────────────────

@pytest.mark.asyncio
async def test_procedural_effectiveness_single(app):
    """get_procedural_effectiveness returns correct stats for a procedural memory."""
    from storage.database import get_session_factory
    from telemetry.procedural_analytics import get_procedural_effectiveness

    factory = get_session_factory()
    async with factory() as session:
        mem = await _make_memory(
            session,
            layer="procedural",
            content="Always verify input before processing",
            project=f"proc-eff-{_id()[:6]}",
            trust_score=0.8,
            times_retrieved=10,
            successful_retrievals=8,
            failed_retrievals=2,
        )
        eff = await get_procedural_effectiveness(session, mem.id)

    assert eff is not None
    assert eff["memory_id"] == mem.id
    assert abs(eff["success_rate"] - 0.8) < 0.01
    assert abs(eff["failure_rate"] - 0.2) < 0.01
    assert eff["times_retrieved"] == 10


@pytest.mark.asyncio
async def test_procedural_effectiveness_no_retrievals(app):
    """Memory with 0 retrievals → success_rate is None."""
    from storage.database import get_session_factory
    from telemetry.procedural_analytics import get_procedural_effectiveness

    factory = get_session_factory()
    async with factory() as session:
        mem = await _make_memory(
            session, layer="procedural",
            content="Untested procedure",
            project=f"proc-zero-{_id()[:6]}",
        )
        eff = await get_procedural_effectiveness(session, mem.id)

    assert eff is not None
    assert eff["success_rate"] is None


@pytest.mark.asyncio
async def test_procedural_effectiveness_non_procedural(app):
    """get_procedural_effectiveness returns None for non-procedural layer."""
    from storage.database import get_session_factory
    from telemetry.procedural_analytics import get_procedural_effectiveness

    factory = get_session_factory()
    async with factory() as session:
        mem = await _make_memory(session, layer="episodic", content="An event")
        eff = await get_procedural_effectiveness(session, mem.id)

    assert eff is None


# ─── Confidence Drift Detection ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_drift_detection_high_failure_rate(app):
    """Memory with >50% failure rate appears in drift candidates."""
    from storage.database import get_session_factory
    from telemetry.procedural_analytics import detect_confidence_drift

    factory = get_session_factory()
    async with factory() as session:
        proj = f"drift-{_id()[:6]}"
        # 1 success, 4 failures = 80% failure rate
        mem = await _make_memory(
            session,
            layer="procedural",
            content="Drifting procedure",
            project=proj,
            trust_score=0.75,
            times_retrieved=5,
            successful_retrievals=1,
            failed_retrievals=4,
        )
        candidates = await detect_confidence_drift(session, project=proj)

    ids = [c["memory_id"] for c in candidates]
    assert mem.id in ids


@pytest.mark.asyncio
async def test_drift_detection_healthy_memory(app):
    """Healthy memory (high success rate) not in drift candidates."""
    from storage.database import get_session_factory
    from telemetry.procedural_analytics import detect_confidence_drift

    factory = get_session_factory()
    async with factory() as session:
        proj = f"nodrift-{_id()[:6]}"
        mem = await _make_memory(
            session,
            content="Reliable procedure",
            project=proj,
            trust_score=0.85,
            times_retrieved=10,
            successful_retrievals=9,
            failed_retrievals=1,
        )
        candidates = await detect_confidence_drift(session, project=proj)

    ids = [c["memory_id"] for c in candidates]
    assert mem.id not in ids


@pytest.mark.asyncio
async def test_drift_trust_decay_applies(app):
    """apply_drift_trust_decay reduces trust on candidates."""
    from storage.database import get_session_factory
    from telemetry.procedural_analytics import detect_confidence_drift, apply_drift_trust_decay

    factory = get_session_factory()
    async with factory() as session:
        proj = f"decay-{_id()[:6]}"
        mem = await _make_memory(
            session,
            content="Decaying procedure",
            project=proj,
            trust_score=0.72,
            times_retrieved=6,
            successful_retrievals=1,
            failed_retrievals=5,
        )
        old_trust = mem.trust_score
        candidates = await detect_confidence_drift(session, project=proj)
        decayed = await apply_drift_trust_decay(session, candidates)

        await session.refresh(mem)
        assert decayed >= 1
        assert mem.trust_score < old_trust
        assert mem.trust_score >= 0.01  # bounded by floor


@pytest.mark.asyncio
async def test_drift_decay_skips_quarantined(app):
    """Drift decay never touches quarantined memories."""
    from storage.database import get_session_factory
    from telemetry.procedural_analytics import apply_drift_trust_decay

    factory = get_session_factory()
    async with factory() as session:
        mem = await _make_memory(
            session,
            memory_state=MemoryState.QUARANTINED,
            trust_score=0.2,
            times_retrieved=5,
            successful_retrievals=0,
            failed_retrievals=5,
        )
        fake_candidates = [{
            "memory_id": mem.id,
            "layer": "procedural",
            "content_snippet": "x",
            "trust_score": 0.2,
            "memory_state": MemoryState.QUARANTINED,
            "times_retrieved": 5,
            "failure_rate": 1.0,
            "recent_failure_rate": 1.0,
            "recent_trust_decreases": 3,
            "recommended_action": "review_and_decay",
        }]
        decayed = await apply_drift_trust_decay(session, fake_candidates)
        assert decayed == 0  # quarantined — not touched


# ─── Telemetry Snapshot Computation ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_compute_snapshot_runs(app):
    """compute_snapshot runs without error and returns a dict of metrics."""
    from storage.database import get_session_factory
    from telemetry.cognition_metrics import compute_snapshot

    factory = get_session_factory()
    async with factory() as session:
        metrics = await compute_snapshot(session, period="daily")

    assert isinstance(metrics, dict)
    assert "retrieval_usefulness_rate" in metrics
    assert "harmful_retrieval_rate" in metrics
    assert "procedural_success_rate" in metrics
    assert "total_memory_count" in metrics
    assert "avg_trust_score" in metrics


@pytest.mark.asyncio
async def test_compute_snapshot_persists(app):
    """compute_snapshot persists TelemetrySnapshot rows to DB."""
    from storage.database import get_session_factory
    from telemetry.cognition_metrics import compute_snapshot

    factory = get_session_factory()
    async with factory() as session:
        metrics = await compute_snapshot(session, period="test")

    factory2 = get_session_factory()
    async with factory2() as session2:
        result = await session2.execute(
            select(TelemetrySnapshot).where(TelemetrySnapshot.period == "test")
        )
        snaps = result.scalars().all()

    assert len(snaps) == len(metrics)
    names = {s.metric_name for s in snaps}
    assert "retrieval_usefulness_rate" in names


@pytest.mark.asyncio
async def test_get_recent_snapshots(app):
    """get_recent_snapshots returns history for a known metric."""
    from storage.database import get_session_factory
    from telemetry.cognition_metrics import compute_snapshot, get_recent_snapshots

    factory = get_session_factory()
    async with factory() as session:
        await compute_snapshot(session, period="hist-test")
        history = await get_recent_snapshots(session, "retrieval_usefulness_rate", limit=5)

    assert isinstance(history, list)
    if history:
        assert "metric_name" in history[0]
        assert "value" in history[0]
        assert "recorded_at" in history[0]


@pytest.mark.asyncio
async def test_get_latest_snapshot(app):
    """get_latest_snapshot returns current values for all metrics."""
    from storage.database import get_session_factory
    from telemetry.cognition_metrics import compute_snapshot, get_latest_snapshot

    factory = get_session_factory()
    async with factory() as session:
        await compute_snapshot(session, period="daily")
        latest = await get_latest_snapshot(session)

    assert isinstance(latest, dict)
    assert len(latest) > 0


# ─── Token Efficiency Analytics ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_token_efficiency_in_snapshot(app):
    """avg_token_efficiency appears in telemetry snapshot after sessions exist."""
    from storage.database import get_session_factory
    from telemetry.cognition_metrics import compute_snapshot

    factory = get_session_factory()
    async with factory() as session:
        # Create a session with known token_efficiency_score
        rs = RetrievalSession(
            id=_id(),
            query="token eff test",
            retrieved_memory_ids=[],
            token_cost=800,
            task_outcome="success",
            token_efficiency_score=0.85,
            inference_applied=True,
        )
        session.add(rs)
        await session.commit()

        metrics = await compute_snapshot(session)

    assert "avg_token_efficiency" in metrics


# ─── Retrieval Usefulness Metrics ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retrieval_session_stats(app):
    """get_retrieval_session_stats returns correct aggregate."""
    from storage.database import get_session_factory
    from telemetry.retrieval_analytics import get_retrieval_session_stats

    factory = get_session_factory()
    async with factory() as session:
        proj = f"stats-{_id()[:6]}"
        for outcome in ["success", "success", "failure"]:
            session.add(RetrievalSession(
                id=_id(), query="q", project=proj,
                retrieved_memory_ids=[], result_count=0, token_cost=500,
                task_outcome=outcome, inference_applied=True,
            ))
        await session.commit()

        stats = await get_retrieval_session_stats(session, project=proj, window_hours=24)

    assert stats["total_sessions"] >= 3
    assert stats["sessions_with_outcome"] >= 3


@pytest.mark.asyncio
async def test_memory_heatmap(app):
    """get_memory_heatmap returns most_used and rarely_used lists."""
    from storage.database import get_session_factory
    from telemetry.retrieval_analytics import get_memory_heatmap

    factory = get_session_factory()
    async with factory() as session:
        proj = f"heatmap-{_id()[:6]}"
        # High-use memory
        await _make_memory(
            session, project=proj, content="Frequently retrieved",
            times_retrieved=50, successful_retrievals=40, failed_retrievals=10
        )
        # Rarely used memory
        await _make_memory(
            session, project=proj, content="Rarely retrieved",
            times_retrieved=1, successful_retrievals=1
        )

        heatmap = await get_memory_heatmap(session, project=proj)

    assert "most_used" in heatmap
    assert "rarely_used" in heatmap
    assert isinstance(heatmap["most_used"], list)


# ─── Rollback Correlation ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rollback_correlation_in_snapshot(app):
    """rollback_correlation metric appears in snapshot."""
    from storage.database import get_session_factory
    from telemetry.cognition_metrics import compute_snapshot

    factory = get_session_factory()
    async with factory() as session:
        metrics = await compute_snapshot(session)

    assert "rollback_correlation" in metrics
    assert "rollback_count" in metrics
    assert 0.0 <= metrics["rollback_correlation"] <= 1.0


# ─── Telemetry API Endpoints ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_telemetry_snapshot_endpoint(client, app):
    """GET /api/telemetry/snapshot returns metrics dict."""
    from tests.conftest import as_user
    with as_user(app, "user-t-1"):
        r = await client.get("/api/telemetry/snapshot")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert "metrics" in data


@pytest.mark.asyncio
async def test_telemetry_compute_endpoint(client, app):
    """POST /api/telemetry/snapshot/compute triggers computation."""
    from tests.conftest import as_user
    with as_user(app, "user-t-2"):
        r = await client.post("/api/telemetry/snapshot/compute")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert isinstance(data["metrics"], dict)


@pytest.mark.asyncio
async def test_telemetry_retrieval_stats_endpoint(client, app):
    """GET /api/telemetry/retrieval/stats returns stats."""
    from tests.conftest import as_user
    with as_user(app, "user-t-3"):
        r = await client.get("/api/telemetry/retrieval/stats")
    assert r.status_code == 200
    data = r.json()
    assert "stats" in data
    assert "total_sessions" in data["stats"]


@pytest.mark.asyncio
async def test_telemetry_heatmap_endpoint(client, app):
    """GET /api/telemetry/retrieval/heatmap returns heatmap."""
    from tests.conftest import as_user
    with as_user(app, "user-t-4"):
        r = await client.get("/api/telemetry/retrieval/heatmap")
    assert r.status_code == 200
    data = r.json()
    assert "heatmap" in data


@pytest.mark.asyncio
async def test_telemetry_procedural_endpoint(client, app):
    """GET /api/telemetry/procedural/effectiveness returns list."""
    from tests.conftest import as_user
    with as_user(app, "user-t-5"):
        r = await client.get("/api/telemetry/procedural/effectiveness")
    assert r.status_code == 200
    data = r.json()
    assert "procedural_memories" in data
    assert isinstance(data["procedural_memories"], list)


@pytest.mark.asyncio
async def test_telemetry_drift_detect_endpoint(client, app):
    """GET /api/telemetry/drift/detect returns candidates list."""
    from tests.conftest import as_user
    with as_user(app, "user-t-6"):
        r = await client.get("/api/telemetry/drift/detect")
    assert r.status_code == 200
    data = r.json()
    assert "drift_candidates" in data
    assert isinstance(data["drift_candidates"], list)


@pytest.mark.asyncio
async def test_telemetry_metric_history_endpoint(client, app):
    """GET /api/telemetry/metrics/{name}/history returns history list."""
    from tests.conftest import as_user
    with as_user(app, "user-t-7"):
        r = await client.get("/api/telemetry/metrics/retrieval_usefulness_rate/history")
    assert r.status_code == 200
    data = r.json()
    assert "history" in data
    assert isinstance(data["history"], list)


# ─── Consolidation Pass Integration ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_consolidation_pass_includes_inference(app):
    """run_consolidation_pass result includes inference keys."""
    from storage.database import get_session_factory
    from worker.consolidator import run_consolidation_pass

    factory = get_session_factory()
    async with factory() as session:
        result = await run_consolidation_pass(session)

    assert "inference_sessions" in result
    assert "inference_positive" in result
    assert "inference_negative" in result


# ─── Safety Constraints ───────────────────────────────────────────────────────

def test_inference_delta_is_small():
    """Inference deltas are small (not massive trust inflation)."""
    from worker.feedback_inference import _INFER_POSITIVE_DELTA, _INFER_NEGATIVE_DELTA
    assert _INFER_POSITIVE_DELTA <= 0.02
    assert _INFER_NEGATIVE_DELTA >= -0.05
    assert _INFER_POSITIVE_DELTA > 0
    assert _INFER_NEGATIVE_DELTA < 0


@pytest.mark.asyncio
async def test_no_auto_reactivation_of_archived(app):
    """Archived memories are not reactivated by positive inference."""
    from storage.database import get_session_factory
    from worker.feedback_inference import infer_retrieval_outcomes

    factory = get_session_factory()
    async with factory() as session:
        proj = f"archived-{_id()[:6]}"
        mem = await _make_memory(
            session, project=proj,
            memory_state=MemoryState.ARCHIVED,
            trust_score=0.3,
        )
        await _make_retrieval_session(
            session, memory_ids=[mem.id], task_outcome="success", project=proj
        )
        await infer_retrieval_outcomes(session)

        await session.refresh(mem)
        assert mem.memory_state == MemoryState.ARCHIVED  # state unchanged
