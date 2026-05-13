"""P15 FTS/keyword isolation tests.

Covers:
  - FTS5 fts5_search filters by user_id at FTS level
  - FTS5 fts5_search filters by project_id at FTS level
  - FTS5 shared memories (user_id=None) visible to all users
  - LIKE fallback is user-scoped (keyword_provider post-filter)
  - Same-project cross-user isolation via keyword provider
  - Same-project cross-user isolation via orchestrated recall
  - FTS triggers preserve user_id and project_id
  - reindex_fts rebuilds with correct user_id/project_id
  - Quarantine trigger removes memory from FTS index
  - keyword_provider with user_id=None has no user restriction
  - Release gate catches keyword leakage
  - Migration 0011 schema has user_id and project_id columns
"""

from __future__ import annotations

import uuid
from datetime import datetime, UTC

import pytest
from sqlalchemy import text

from storage.database import get_session_factory
from storage.fts import fts5_search, reset_fts5_probe, reindex_fts
from storage.models import Memory


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _mid() -> str:
    return uuid.uuid4().hex


def _uid(prefix: str = "p15") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


async def _store_memory(
    session,
    content: str,
    *,
    user_id: str | None = None,
    project: str | None = None,
    layer: str = "semantic",
    memory_state: str = "active",
) -> Memory:
    mem = Memory(
        id=_mid(),
        layer=layer,
        content=content,
        importance=0.7,
        project=project,
        user_id=user_id,
        memory_state=memory_state,
        trust_score=0.7,
        verification_status="trusted_system_observed",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(mem)
    await session.commit()
    return mem


async def _insert_fts(session, mem: Memory) -> None:
    """Manually insert a memory into FTS5 (mimics the trigger)."""
    await session.execute(text(
        "INSERT OR REPLACE INTO memory_fts(memory_id, user_id, project_id, content) "
        "VALUES (:mid, :uid, :pid, :content)"
    ), {
        "mid": mem.id,
        "uid": mem.user_id or "",
        "pid": mem.project or "",
        "content": mem.content,
    })
    await session.commit()


# ─── FTS5 schema check ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fts_schema_has_user_id_and_project_id(app):
    """Post-0011 FTS5 table must have user_id and project_id columns."""
    reset_fts5_probe()
    factory = get_session_factory()
    async with factory() as session:
        # If user_id/project_id columns exist, this select won't raise
        try:
            result = await session.execute(text(
                "SELECT memory_id, user_id, project_id FROM memory_fts LIMIT 1"
            ))
            rows = result.fetchall()
            # Success: new schema is in place
            assert True
        except Exception as e:
            pytest.fail(f"FTS5 table missing user_id/project_id columns: {e}")


# ─── FTS5 user_id isolation ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fts_search_filters_by_user_id(app):
    """fts5_search(user_id=X) must not return memories owned by user Y."""
    reset_fts5_probe()
    project = _uid("fts_uid")
    unique_token = _uid("FTSUSR")
    content = f"{unique_token} confidential data for alpha only"

    factory = get_session_factory()
    async with factory() as session:
        mem = await _store_memory(
            session, content,
            user_id="fts_alpha", project=project,
        )
        await _insert_fts(session, mem)

        # Alpha can find it
        alpha_hits = await fts5_search(
            session, unique_token,
            user_id="fts_alpha", project_id=project,
        )
        alpha_ids = [h[0] for h in alpha_hits]
        assert mem.id in alpha_ids, "alpha should find own memory"

        # Beta cannot find it (different user, same project)
        beta_hits = await fts5_search(
            session, unique_token,
            user_id="fts_beta", project_id=project,
        )
        beta_ids = [h[0] for h in beta_hits]
        assert mem.id not in beta_ids, \
            f"beta must not see alpha's memory: {beta_ids}"


@pytest.mark.asyncio
async def test_fts_search_shared_memory_visible_to_all(app):
    """Memories with user_id=None (shared) are visible to all users."""
    reset_fts5_probe()
    project = _uid("fts_shared")
    unique_token = _uid("FTSSHARED")
    content = f"{unique_token} shared project-level memory"

    factory = get_session_factory()
    async with factory() as session:
        mem = await _store_memory(
            session, content,
            user_id=None, project=project,
        )
        await _insert_fts(session, mem)

        # User alpha can see it
        hits = await fts5_search(
            session, unique_token,
            user_id="any_user", project_id=project,
        )
        ids = [h[0] for h in hits]
        assert mem.id in ids, "shared memory must be visible to any user"


# ─── FTS5 project_id isolation ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fts_search_filters_by_project_id(app):
    """fts5_search(project_id=P1) must not return memories from project P2."""
    reset_fts5_probe()
    unique_token = _uid("FTSPROJ")
    content = f"{unique_token} project-scoped memory"

    factory = get_session_factory()
    async with factory() as session:
        mem = await _store_memory(
            session, content,
            user_id=None, project="fts_project_one",
        )
        await _insert_fts(session, mem)

        # Correct project finds it
        hits = await fts5_search(
            session, unique_token,
            project_id="fts_project_one",
        )
        assert mem.id in [h[0] for h in hits], "correct project should find memory"

        # Wrong project does not
        hits2 = await fts5_search(
            session, unique_token,
            project_id="fts_project_two",
        )
        assert mem.id not in [h[0] for h in hits2], \
            "different project must not see memory"


# ─── Keyword provider isolation (same project, different users) ───────────────

@pytest.mark.asyncio
async def test_keyword_provider_same_project_user_isolation(app):
    """keyword_provider must not return user A's memory to user B in the same project."""
    reset_fts5_probe()
    from retrieval.providers import keyword_provider

    shared_project = _uid("kw_iso")
    unique_token = _uid("KWISO")
    content = f"{unique_token} private data for user alpha only"

    factory = get_session_factory()
    async with factory() as session:
        mem = await _store_memory(
            session, content,
            user_id="kw_alpha", project=shared_project,
        )
        await _insert_fts(session, mem)

        # Alpha finds own memory
        alpha_hits = await keyword_provider(
            session, unique_token,
            project=shared_project, user_id="kw_alpha",
        )
        alpha_ids = [h.memory_id for h in alpha_hits]
        assert mem.id in alpha_ids, "alpha must find own memory"

        # Beta in same project must not see alpha's memory
        beta_hits = await keyword_provider(
            session, unique_token,
            project=shared_project, user_id="kw_beta",
        )
        beta_ids = [h.memory_id for h in beta_hits]
        assert mem.id not in beta_ids, \
            f"beta must not see alpha's private memory in keyword results"


@pytest.mark.asyncio
async def test_keyword_provider_like_fallback_user_scoped(app):
    """LIKE fallback path also filters by user_id."""
    from retrieval.providers import keyword_provider
    from storage.fts import reset_fts5_probe
    import storage.fts as fts_module

    shared_project = _uid("like_iso")
    unique_token = _uid("LIKEISO")
    content = f"{unique_token} private data like fallback test"

    factory = get_session_factory()
    async with factory() as session:
        mem = await _store_memory(
            session, content,
            user_id="like_alpha", project=shared_project,
        )
        # Don't insert into FTS to force LIKE path
        # Temporarily disable FTS5
        original = fts_module._FTS5_AVAILABLE
        fts_module._FTS5_AVAILABLE = False
        try:
            # Alpha finds own memory via LIKE
            alpha_hits = await keyword_provider(
                session, unique_token,
                project=shared_project, user_id="like_alpha",
            )
            alpha_ids = [h.memory_id for h in alpha_hits]
            assert mem.id in alpha_ids, "alpha must find own memory via LIKE"

            # Beta must not see alpha's memory via LIKE
            beta_hits = await keyword_provider(
                session, unique_token,
                project=shared_project, user_id="like_beta",
            )
            beta_ids = [h.memory_id for h in beta_hits]
            assert mem.id not in beta_ids, \
                "beta must not see alpha's memory via LIKE fallback"
        finally:
            fts_module._FTS5_AVAILABLE = original


# ─── Full recall pipeline isolation (same project, different users) ────────────

@pytest.mark.asyncio
async def test_recall_same_project_keyword_isolation(client):
    """End-to-end recall: keyword path must not leak across users in same project."""
    shared_project = _uid("recall_iso")
    unique_token = _uid("RECISO")

    # Store alpha's private memory
    r = await client.post("/api/memory", json={
        "layer": "semantic",
        "content": f"{unique_token} alpha private information.",
        "project": shared_project,
        "user_id": "recall_alpha",
    })
    assert r.status_code == 200
    alpha_id = r.json()["id"]

    # Beta queries in the same project — must not see alpha's memory
    r2 = await client.post("/api/events/recall", json={
        "query": unique_token,
        "project": shared_project,
        "user_id": "recall_beta",
        "limit": 50,
    })
    assert r2.status_code == 200
    hit_ids = [h["id"] for h in r2.json().get("hits", [])]
    assert alpha_id not in hit_ids, \
        "keyword path leaked alpha's memory to beta in same-project recall"


@pytest.mark.asyncio
async def test_recall_orchestrated_same_project_isolation(client):
    """Orchestrated context (token_budget) must not leak keyword results cross-user."""
    shared_project = _uid("orch_iso")
    unique_token = _uid("ORCHISO")

    r = await client.post("/api/memory", json={
        "layer": "semantic",
        "content": f"{unique_token} alpha orchestrated private data.",
        "project": shared_project,
        "user_id": "orch_alpha",
    })
    assert r.status_code == 200
    alpha_id = r.json()["id"]

    r2 = await client.post("/api/events/recall", json={
        "query": unique_token,
        "project": shared_project,
        "user_id": "orch_beta",
        "token_budget": 2000,
        "limit": 50,
    })
    assert r2.status_code == 200
    data = r2.json()
    # Check raw hits
    hit_ids = [h["id"] for h in data.get("hits", [])]
    assert alpha_id not in hit_ids, "orchestrated raw hits leaked alpha's memory"
    # Check context memories
    ctx_ids = [
        m["id"] if isinstance(m, dict) else m
        for m in data.get("context", {}).get("memories", [])
        if isinstance(m, (dict, str))
    ]
    assert alpha_id not in ctx_ids, "orchestrated context leaked alpha's memory"


# ─── FTS trigger tests ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fts_insert_trigger_preserves_user_id(app):
    """After INSERT via API, memory_fts row must have correct user_id."""
    reset_fts5_probe()
    unique_token = _uid("TRGINS")
    project = _uid("trg")

    factory = get_session_factory()
    async with factory() as session:
        mem = await _store_memory(
            session,
            f"{unique_token} trigger insert test",
            user_id="trigger_user_a",
            project=project,
        )
        # The AFTER INSERT trigger should have written to memory_fts
        result = await session.execute(text(
            "SELECT user_id, project_id FROM memory_fts WHERE memory_id = :mid"
        ), {"mid": mem.id})
        row = result.fetchone()
        assert row is not None, "FTS row not created by INSERT trigger"
        assert row[0] == "trigger_user_a", \
            f"FTS user_id={row[0]} != 'trigger_user_a'"
        assert row[1] == project, \
            f"FTS project_id={row[1]} != '{project}'"


@pytest.mark.asyncio
async def test_fts_softdelete_trigger_removes_row(app):
    """Soft-deleting a memory must remove it from the FTS index."""
    reset_fts5_probe()
    factory = get_session_factory()
    async with factory() as session:
        mem = await _store_memory(
            session, f"soft delete test {_uid()}",
            user_id="del_user", project=_uid("del"),
        )
        # Verify in FTS
        result = await session.execute(text(
            "SELECT memory_id FROM memory_fts WHERE memory_id = :mid"
        ), {"mid": mem.id})
        assert result.fetchone() is not None, "FTS row should exist before delete"

        # Soft-delete
        mem.deleted_at = datetime.now(UTC)
        session.add(mem)
        await session.commit()

        # Must be removed from FTS
        result2 = await session.execute(text(
            "SELECT memory_id FROM memory_fts WHERE memory_id = :mid"
        ), {"mid": mem.id})
        assert result2.fetchone() is None, "FTS row must be removed after soft-delete"


@pytest.mark.asyncio
async def test_fts_quarantine_trigger_removes_row(app):
    """Quarantining a memory must remove it from the FTS index."""
    reset_fts5_probe()
    factory = get_session_factory()
    async with factory() as session:
        mem = await _store_memory(
            session, f"quarantine fts test {_uid()}",
            user_id="quar_user", project=_uid("quar"),
        )
        await _insert_fts(session, mem)

        result = await session.execute(text(
            "SELECT memory_id FROM memory_fts WHERE memory_id = :mid"
        ), {"mid": mem.id})
        assert result.fetchone() is not None, "FTS row should exist pre-quarantine"

        # Quarantine it
        mem.memory_state = "quarantined"
        session.add(mem)
        await session.commit()

        result2 = await session.execute(text(
            "SELECT memory_id FROM memory_fts WHERE memory_id = :mid"
        ), {"mid": mem.id})
        assert result2.fetchone() is None, \
            "FTS row must be removed when memory is quarantined"


# ─── reindex_fts ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reindex_fts_preserves_user_id_and_project(app):
    """reindex_fts must restore user_id and project_id from the memories table."""
    reset_fts5_probe()
    unique_token = _uid("REINDEX")
    project = _uid("reidx")

    factory = get_session_factory()
    async with factory() as session:
        mem = await _store_memory(
            session, f"{unique_token} reindex isolation test",
            user_id="reindex_user", project=project,
        )

        # Blow away the FTS row to simulate stale index
        await session.execute(text(
            "DELETE FROM memory_fts WHERE memory_id = :mid"
        ), {"mid": mem.id})
        await session.commit()

        # Rebuild
        count = await reindex_fts(session)
        assert count >= 1, f"reindex returned {count}"

        # Verify row is back with correct isolation columns
        result = await session.execute(text(
            "SELECT user_id, project_id FROM memory_fts WHERE memory_id = :mid"
        ), {"mid": mem.id})
        row = result.fetchone()
        assert row is not None, "FTS row not restored by reindex_fts"
        assert row[0] == "reindex_user", f"user_id={row[0]}"
        assert row[1] == project, f"project_id={row[1]}"


@pytest.mark.asyncio
async def test_reindex_fts_excludes_quarantined(app):
    """reindex_fts must not index quarantined memories."""
    reset_fts5_probe()
    factory = get_session_factory()
    async with factory() as session:
        mem = await _store_memory(
            session, f"quarantined reindex test {_uid()}",
            user_id="qreindex_user",
            memory_state="quarantined",
        )
        # Force it into FTS to simulate stale index
        await session.execute(text(
            "INSERT OR REPLACE INTO memory_fts(memory_id, user_id, project_id, content) "
            "VALUES (:mid, :uid, '', :content)"
        ), {"mid": mem.id, "uid": "qreindex_user", "content": mem.content})
        await session.commit()

        # Reindex should clear the quarantined row
        await reindex_fts(session)

        result = await session.execute(text(
            "SELECT memory_id FROM memory_fts WHERE memory_id = :mid"
        ), {"mid": mem.id})
        assert result.fetchone() is None, \
            "quarantined memory must not appear in FTS after reindex"


# ─── Debug exclusion does not expose other users ──────────────────────────────

@pytest.mark.asyncio
async def test_orchestrator_debug_excluded_no_cross_user(client):
    """The excluded[] list in orchestrator debug must not contain other users' memories."""
    shared_project = _uid("dbg_iso")
    unique_token = _uid("DBGISO")

    r = await client.post("/api/memory", json={
        "layer": "semantic",
        "content": f"{unique_token} debug exclusion alpha data.",
        "project": shared_project,
        "user_id": "dbg_alpha",
    })
    assert r.status_code == 200
    alpha_id = r.json()["id"]

    r2 = await client.post("/api/events/recall", json={
        "query": unique_token,
        "project": shared_project,
        "user_id": "dbg_beta",
        "token_budget": 2000,
    })
    assert r2.status_code == 200
    data = r2.json()
    debug = data.get("debug", {})
    excluded_ids = [e.get("id") for e in debug.get("excluded", []) if isinstance(e, dict)]
    selected_ids = [s.get("id") for s in debug.get("selected", []) if isinstance(s, dict)]
    all_returned = set(excluded_ids) | set(selected_ids)
    assert alpha_id not in all_returned, \
        "alpha's memory appeared in beta's debug output (excluded or selected)"


# ─── No-user-id open access ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_keyword_provider_no_user_id_returns_shared_memories(app):
    """When user_id=None, keyword provider returns shared (user_id=None) memories."""
    reset_fts5_probe()
    from retrieval.providers import keyword_provider

    project = _uid("shared")
    unique_token = _uid("SHARED")
    content = f"{unique_token} shared memory no user"

    factory = get_session_factory()
    async with factory() as session:
        mem = await _store_memory(
            session, content,
            user_id=None, project=project,
        )
        await _insert_fts(session, mem)

        hits = await keyword_provider(
            session, unique_token,
            project=project, user_id=None,
        )
        ids = [h.memory_id for h in hits]
        assert mem.id in ids, "shared memory must appear when user_id=None"


# ─── Migration schema verification ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_migration_0011_fts_columns_exist(app):
    """Verifies migration 0011 created the user_id and project_id columns in memory_fts."""
    factory = get_session_factory()
    async with factory() as session:
        # table_info for FTS5 virtual tables
        result = await session.execute(text("SELECT * FROM memory_fts LIMIT 0"))
        col_names = [desc[0] for desc in result.cursor.description]
        assert "user_id" in col_names, f"user_id missing from memory_fts columns: {col_names}"
        assert "project_id" in col_names, f"project_id missing from memory_fts columns: {col_names}"
        assert "memory_id" in col_names
        assert "content" in col_names
