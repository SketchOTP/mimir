"""End-to-end scenario tests.

test_full_e2e_scenario covers the complete Mimir lifecycle:
  store preferred name → recall → reflect → propose → approve → promote →
  record degraded metric → auto-rollback → verify rollback record

test_backfill_promoted_at verifies that old promoted improvements gain
promoted_at via backfill so the rollback watcher can process them.
"""

import pytest
import uuid
from datetime import datetime, timedelta

from storage.models import ImprovementProposal, MetricRecord, Rollback
from approvals import promotion_worker, rollback_watcher


@pytest.mark.asyncio
async def test_full_e2e_scenario(client, app):
    from storage.database import get_session_factory

    project = f"e2e_{uuid.uuid4().hex[:8]}"

    # ── 1. Store preferred name ───────────────────────────────────────────────
    r = await client.post("/api/events", json={
        "type": "user_correction",
        "correction": "Call me Tym, not Tim",
        "project": project,
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("ok") is True
    assert len(data.get("stored", [])) >= 1, "Expected at least one memory stored"

    # ── 2. Retrieve preferred name ────────────────────────────────────────────
    r = await client.post("/api/events/recall", json={
        "query": "preferred name",
        "project": project,
    })
    assert r.status_code == 200
    recall = r.json()
    # Without token_budget returns {hits:[]}; with token_budget returns context dict
    assert "hits" in recall or "memories" in recall or "context" in recall

    # ── 3. Log a retrieval-miss reflection ────────────────────────────────────
    r = await client.post("/api/reflections", json={
        "trigger": "retrieval_miss",
        "observations": ["Preferred name was not included in the context"],
        "lessons": ["Identity memories need a priority boost in the ranker"],
        "project": project,
    })
    assert r.status_code == 200
    assert "id" in r.json()

    # ── 4. Propose an improvement ─────────────────────────────────────────────
    r = await client.post("/api/improvements/propose", json={
        "improvement_type": "memory_policy",
        "title": "Boost identity memory priority",
        "reason": "Preferred name was not retrieved when relevant",
        "current_behavior": "Identity memories ranked equally to other memories",
        "proposed_behavior": "Identity memories receive +0.3 importance boost",
        "expected_benefit": "Preferred name always surfaces in context",
        "risk": "low",
        "project": project,
    })
    assert r.status_code == 200
    imp_id = r.json()["id"]

    # ── 5. Request approval ───────────────────────────────────────────────────
    r = await client.post(f"/api/approvals?improvement_id={imp_id}", json={})
    assert r.status_code == 200
    approval_id = r.json()["approval"]["id"]

    # ── 6. Approve ────────────────────────────────────────────────────────────
    r = await client.post(f"/api/approvals/{approval_id}/approve", json={"reviewer_note": "LGTM"})
    assert r.status_code == 200
    assert r.json()["status"] == "approved"

    # ── 7. Promote approved change ────────────────────────────────────────────
    async with get_session_factory()() as session:
        promoted = await promotion_worker.promote_approved(session)
        assert imp_id in promoted, f"{imp_id} not in promoted list: {promoted}"

    # ── 8. Verify promoted_at is set ─────────────────────────────────────────
    async with get_session_factory()() as session:
        imp = await session.get(ImprovementProposal, imp_id)
        assert imp is not None
        assert imp.status == "promoted"
        assert imp.meta and "promoted_at" in imp.meta, "promoted_at must be set after promotion"

    # ── 9. Insert metric records showing post-promotion degradation ───────────
    # Use naive datetimes throughout to match SQLite's DateTime storage.
    # Set promoted_at to a controlled naive time so the query windows line up.
    test_promoted_at = datetime.now() - timedelta(hours=1)  # 1h ago, naive
    before_time = test_promoted_at - timedelta(hours=2)     # within the 24h look-back
    after_time = test_promoted_at + timedelta(minutes=30)   # after promotion

    async with get_session_factory()() as session:
        imp = await session.get(ImprovementProposal, imp_id)
        imp.meta = {**imp.meta, "promoted_at": test_promoted_at.isoformat()}
        await session.commit()

    metric_name = "retrieval_relevance_score"
    async with get_session_factory()() as session:
        session.add(MetricRecord(
            id=f"e2e_before_{uuid.uuid4().hex[:8]}",
            name=metric_name,
            value=0.88,
            recorded_at=before_time,
        ))
        session.add(MetricRecord(
            id=f"e2e_after_{uuid.uuid4().hex[:8]}",
            name=metric_name,
            value=0.70,  # drop of 0.18 > threshold of 0.10 → triggers rollback
            recorded_at=after_time,
        ))
        await session.commit()

    # ── 10. Auto-rollback ─────────────────────────────────────────────────────
    async with get_session_factory()() as session:
        rolled_back = await rollback_watcher.watch_and_rollback(session)
        assert imp_id in rolled_back, (
            f"Expected {imp_id} to be rolled back. Got: {rolled_back}"
        )

    # ── 11. Verify rollback record ────────────────────────────────────────────
    async with get_session_factory()() as session:
        imp = await session.get(ImprovementProposal, imp_id)
        assert imp.status == "rolled_back"

        from sqlalchemy import select
        rb_q = await session.execute(
            select(Rollback).where(Rollback.target_id == imp_id)
        )
        rollback = rb_q.scalars().first()
        assert rollback is not None, "Rollback record must exist"
        assert rollback.automatic is True
        assert metric_name in rollback.metrics_before
        assert metric_name in rollback.metrics_after
        assert rollback.reason is not None and "degraded" in rollback.reason.lower()


# ── Promotion backfill test ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_backfill_promoted_at(client, app):
    """backfill_promoted_at() infers and writes promoted_at for old promotions."""
    from storage.database import get_session_factory
    from approvals.promotion_worker import backfill_promoted_at

    # Create and fully approve an improvement
    r = await client.post("/api/improvements/propose", json={
        "improvement_type": "context_tune",
        "title": "Backfill test improvement",
        "reason": "testing the backfill utility",
        "current_behavior": "default context",
        "proposed_behavior": "tuned context",
        "expected_benefit": "fewer irrelevant memories",
    })
    assert r.status_code == 200
    imp_id = r.json()["id"]

    r = await client.post(f"/api/approvals?improvement_id={imp_id}", json={})
    approval_id = r.json()["approval"]["id"]
    await client.post(f"/api/approvals/{approval_id}/approve", json={})

    # Manually set status to "promoted" but wipe promoted_at to simulate old data
    async with get_session_factory()() as session:
        imp = await session.get(ImprovementProposal, imp_id)
        imp.status = "promoted"
        imp.meta = {}  # no promoted_at
        await session.commit()

    # Run backfill
    async with get_session_factory()() as session:
        count = await backfill_promoted_at(session)
        assert count >= 1, f"Expected ≥1 improvement backfilled, got {count}"

    # Verify promoted_at is now a valid ISO datetime
    async with get_session_factory()() as session:
        imp = await session.get(ImprovementProposal, imp_id)
        assert imp.meta and "promoted_at" in imp.meta, "promoted_at must exist after backfill"
        datetime.fromisoformat(imp.meta["promoted_at"])  # must parse without error
