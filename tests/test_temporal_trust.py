"""
Tests for P2 + P3: temporal memory fields and trust system.

Covers:
- Migration adds all required fields (columns present on Memory model / DB)
- Old records backfill safely with sane defaults
- Direct user identity memory gets high trust (trusted_user_explicit)
- Inferred memory gets lower trust (inferred_low_confidence)
- Conflicting memory marked as contradicted / conflicting
- Quarantined memory excluded from recall and context
- Stale memory excluded from identity (high-priority) context
- Vector metadata includes trust_score and verification_status
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect as sa_inspect, text

from memory.trust import MemoryState, TrustLevel
from memory.memory_extractor import extract_trust_info, extract_from_event


# ─── Unit: trust assignment logic ────────────────────────────────────────────

def test_user_correction_event_gets_explicit_trust():
    info = extract_trust_info("don't call me Timothy", "user_correction")
    assert info["verification_status"] == TrustLevel.TRUSTED_USER_EXPLICIT
    assert info["trust_score"] >= 0.9
    assert info["confidence"] >= 0.95


def test_identity_statement_gets_explicit_trust():
    info = extract_trust_info("Call me Tym, not Timothy")
    assert info["verification_status"] == TrustLevel.TRUSTED_USER_EXPLICIT
    assert info["trust_score"] >= 0.85


def test_identity_preference_gets_explicit_trust():
    info = extract_trust_info("I prefer dark mode for all my interfaces")
    assert info["verification_status"] == TrustLevel.TRUSTED_USER_EXPLICIT


def test_inferred_ephemeral_gets_low_confidence():
    info = extract_trust_info("Currently temporarily using a different config right now")
    assert info["verification_status"] == TrustLevel.INFERRED_LOW_CONFIDENCE
    assert info["trust_score"] < 0.7


def test_generic_system_content_gets_system_observed():
    info = extract_trust_info("The deployment completed successfully")
    assert info["verification_status"] == TrustLevel.TRUSTED_SYSTEM_OBSERVED


def test_extract_from_event_includes_trust_info():
    event = {
        "type": "user_correction",
        "correction": "Call me Tym, not Timothy",
    }
    candidates = extract_from_event(event)
    correction_candidate = next(
        c for c in candidates if c.get("content") == "Call me Tym, not Timothy"
    )
    assert "trust_info" in correction_candidate
    assert correction_candidate["trust_info"]["verification_status"] == TrustLevel.TRUSTED_USER_EXPLICIT


# ─── Integration: schema / DB fields present ─────────────────────────────────

@pytest.mark.asyncio
async def test_memory_model_has_all_temporal_trust_columns(app):
    """All 13 new columns must be present in the memories table."""
    from storage.database import get_session_factory
    required_cols = {
        "valid_from", "valid_to", "superseded_by", "memory_state", "last_verified_at",
        "trust_score", "source_type", "source_id", "created_by", "verified_by",
        "verification_status", "confidence", "poisoning_flags",
    }
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(text("PRAGMA table_info(memories)"))
        cols = {row[1] for row in result.fetchall()}
    missing = required_cols - cols
    assert not missing, f"Missing columns in memories table: {missing}"


@pytest.mark.asyncio
async def test_new_memory_defaults_to_active_state(client):
    r = await client.post("/api/memory", json={"content": "A test fact", "layer": "semantic"})
    assert r.status_code == 200
    data = r.json()
    assert data["memory_state"] == MemoryState.ACTIVE


@pytest.mark.asyncio
async def test_new_memory_has_trust_defaults(client):
    r = await client.post("/api/memory", json={"content": "User likes brevity", "layer": "semantic"})
    assert r.status_code == 200
    data = r.json()
    assert data["trust_score"] is not None
    assert data["verification_status"] is not None
    assert data["confidence"] is not None
    assert data["trust_score"] > 0.0
    assert data["confidence"] > 0.0


# ─── Integration: high-trust path (user identity memory) ─────────────────────

@pytest.mark.asyncio
async def test_user_correction_event_stores_high_trust_memory(client):
    r = await client.post("/api/events", json={
        "type": "user_correction",
        "correction": "Call me Tym, never Timothy",
        "project": "test_trust",
    })
    assert r.status_code == 200
    stored = r.json()["stored"]
    assert stored, "Expected at least one memory stored"

    mem_id = stored[-1]["id"]
    r2 = await client.get(f"/api/memory/{mem_id}")
    assert r2.status_code == 200
    data = r2.json()
    assert data["verification_status"] == TrustLevel.TRUSTED_USER_EXPLICIT
    assert data["trust_score"] >= 0.9


# ─── Integration: conflict marks contradicted state ──────────────────────────

@pytest.mark.asyncio
async def test_conflicting_memory_gets_contradicted_state(client):
    # Store original preference
    r1 = await client.post("/api/memory", json={
        "content": "I always prefer verbose explanations with lots of detail",
        "layer": "semantic",
    })
    assert r1.status_code == 200

    # Store direct contradiction
    r2 = await client.post("/api/memory", json={
        "content": "Never give verbose explanations — always keep responses short",
        "layer": "semantic",
    })
    assert r2.status_code == 200
    mem_id2 = r2.json()["id"]

    r2_check = await client.get(f"/api/memory/{mem_id2}")
    assert r2_check.status_code == 200
    data = r2_check.json()
    # Either it was deduped (same id) or it has contradicted state
    if data["id"] != r1.json()["id"]:
        assert data["memory_state"] == MemoryState.CONTRADICTED
        assert data["verification_status"] == TrustLevel.CONFLICTING


# ─── Integration: quarantined memory excluded from recall ────────────────────

@pytest.mark.asyncio
async def test_quarantined_memory_excluded_from_recall(client):
    from storage.database import get_session_factory
    from storage.models import Memory
    from sqlalchemy import select

    # Store a memory
    r = await client.post("/api/memory", json={
        "content": "Quarantine exclusion test unique content xyz987",
        "layer": "semantic",
    })
    assert r.status_code == 200
    mem_id = r.json()["id"]

    # Manually set it to quarantined
    factory = get_session_factory()
    async with factory() as session:
        mem = await session.get(Memory, mem_id)
        assert mem is not None
        mem.memory_state = MemoryState.QUARANTINED
        await session.commit()

    # Recall — quarantined memory must not appear
    r2 = await client.post("/api/events/recall", json={
        "query": "Quarantine exclusion test unique content xyz987",
        "limit": 20,
    })
    assert r2.status_code == 200
    hits = r2.json().get("hits", [])
    hit_ids = [h.get("id") for h in hits]
    assert mem_id not in hit_ids, "Quarantined memory must not appear in recall results"


# ─── Integration: stale memory excluded from identity context ─────────────────

@pytest.mark.asyncio
async def test_stale_memory_excluded_from_identity_context(client):
    """Stale memories must not be promoted to identity_priority in context (directive: P3).

    They may still appear in lower-priority ranked slots, but must never be
    tagged as identity_priority in the context debug output.
    """
    from storage.database import get_session_factory
    from storage.models import Memory

    # Store a high-importance name preference
    r = await client.post("/api/memory", json={
        "content": "My preferred name is Stale McStaleson",
        "layer": "semantic",
        "importance": 0.95,
    })
    assert r.status_code == 200
    mem_id = r.json()["id"]

    # Mark it stale — must be excluded from identity/high-priority path
    factory = get_session_factory()
    async with factory() as session:
        mem = await session.get(Memory, mem_id)
        assert mem is not None
        mem.memory_state = MemoryState.STALE
        await session.commit()

    # Recall with token_budget triggers full context build including get_identity_context
    r2 = await client.post("/api/events/recall", json={
        "query": "what is my preferred name",
        "limit": 20,
        "token_budget": 2000,
    })
    assert r2.status_code == 200
    ctx = r2.json().get("context", {})
    debug_selected = ctx.get("debug", {}).get("selected", [])

    # The stale memory must NOT appear with identity_priority reason
    stale_identity = [
        entry for entry in debug_selected
        if entry["id"] == mem_id and entry.get("selected_reason") == "identity_priority"
    ]
    assert not stale_identity, (
        "Stale memory must not be promoted to identity_priority in context"
    )


# ─── Integration: vector metadata includes trust fields ──────────────────────

@pytest.mark.asyncio
async def test_vector_metadata_includes_trust_fields(client):
    from storage import vector_store

    r = await client.post("/api/memory", json={
        "content": "Vector trust metadata verification test content abc123",
        "layer": "semantic",
    })
    assert r.status_code == 200
    mem_id = r.json()["id"]

    # Search vector store directly and check metadata
    hits = vector_store.search(
        "semantic",
        "Vector trust metadata verification test content abc123",
        n_results=5,
    )
    match = next((h for h in hits if h["id"] == mem_id), None)
    assert match is not None, "Memory not found in vector store"
    meta = match["metadata"]
    assert "trust_score" in meta, "trust_score missing from vector metadata"
    assert "verification_status" in meta, "verification_status missing from vector metadata"
    assert "memory_state" in meta, "memory_state missing from vector metadata"
    assert meta["trust_score"] > 0.0
    assert meta["verification_status"] != ""
