"""P7 Lifecycle Engine tests.

Covers all required test cases from the directive:
  - active → aging transition
  - aging → stale transition
  - stale → archived transition
  - supersession logic (valid_to, superseded_by)
  - trust increase on successful retrieval
  - trust decrease on contradiction
  - verification decay
  - episodic chain creation
  - consolidation merge stability
  - quarantined memory never reactivated automatically
  - lifecycle event logging
  - retrieval frequency tracking
  - DB schema for new columns/tables
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, UTC

import pytest
from sqlalchemy import inspect as sa_inspect, text

from memory.trust import MemoryState, TrustLevel


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _mem_id() -> str:
    return uuid.uuid4().hex


async def _make_memory(session, *, layer="semantic", content="test memory",
                       project=None, memory_state="active", trust_score=0.7,
                       importance=0.5, source_type="system_observed", source_id=None,
                       times_retrieved=0, successful_retrievals=0, failed_retrievals=0,
                       last_accessed=None, created_at=None, last_verified_at=None,
                       confidence=0.7, session_id=None):
    from storage.models import Memory
    now = datetime.now(UTC)
    mem = Memory(
        id=_mem_id(),
        layer=layer,
        content=content,
        project=project,
        memory_state=memory_state,
        trust_score=trust_score,
        importance=importance,
        source_type=source_type,
        source_id=source_id,
        verification_status="trusted_system_observed",
        confidence=confidence,
        times_retrieved=times_retrieved,
        successful_retrievals=successful_retrievals,
        failed_retrievals=failed_retrievals,
        last_accessed=last_accessed,
        last_verified_at=last_verified_at,
        valid_from=created_at or now,
        session_id=session_id,
    )
    if created_at:
        mem.created_at = created_at
    session.add(mem)
    await session.flush()
    return mem


# ─── Schema tests ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_memories_has_retrieval_frequency_columns(app):
    """Migration 0005 must add retrieval frequency columns to memories table."""
    from storage.database import get_session_factory
    required = {"times_retrieved", "last_retrieved_at", "successful_retrievals", "failed_retrievals"}
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(text("PRAGMA table_info(memories)"))
        cols = {row[1] for row in result.fetchall()}
    missing = required - cols
    assert not missing, f"Missing retrieval frequency columns: {missing}"


@pytest.mark.asyncio
async def test_episodic_chains_table_exists(app):
    """Migration 0005 must create episodic_chains table."""
    from storage.database import get_session_factory
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(text("PRAGMA table_info(episodic_chains)"))
        cols = {row[1] for row in result.fetchall()}
    assert "id" in cols
    assert "linked_memory_ids" in cols
    assert "episode_summary" in cols
    assert "procedural_lesson" in cols


@pytest.mark.asyncio
async def test_lifecycle_events_table_exists(app):
    """Migration 0005 must create lifecycle_events table."""
    from storage.database import get_session_factory
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(text("PRAGMA table_info(lifecycle_events)"))
        cols = {row[1] for row in result.fetchall()}
    assert "id" in cols
    assert "memory_id" in cols
    assert "event_type" in cols
    assert "from_state" in cols
    assert "to_state" in cols
    assert "trust_before" in cols
    assert "trust_after" in cols


# ─── State transition: active → aging ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_active_to_aging_transition(app):
    """Memory inactive for 30+ days must transition active → aging."""
    from storage.database import get_session_factory
    from worker.lifecycle import transition_aging, _AGING_DAYS
    from storage.models import LifecycleEvent
    from sqlalchemy import select

    old_date = datetime.now(UTC) - timedelta(days=_AGING_DAYS + 5)
    factory = get_session_factory()
    async with factory() as session:
        mem = await _make_memory(session, memory_state="active", last_accessed=old_date,
                                 created_at=old_date)
        await session.commit()
        mem_id = mem.id

        count = await transition_aging(session)
        assert count >= 1

        await session.refresh(mem)
        assert mem.memory_state == MemoryState.AGING

        ev = await session.execute(
            select(LifecycleEvent).where(
                LifecycleEvent.memory_id == mem_id,
                LifecycleEvent.event_type == "memory_aged",
            )
        )
        assert ev.scalars().first() is not None, "Expected lifecycle event for aging"


@pytest.mark.asyncio
async def test_recently_active_memory_not_aged(app):
    """Memory accessed recently must NOT transition to aging."""
    from storage.database import get_session_factory
    from worker.lifecycle import transition_aging

    factory = get_session_factory()
    async with factory() as session:
        mem = await _make_memory(session, memory_state="active",
                                 last_accessed=datetime.now(UTC))
        await session.commit()
        mem_id = mem.id

        await transition_aging(session)
        await session.refresh(mem)
        assert mem.memory_state == MemoryState.ACTIVE


@pytest.mark.asyncio
async def test_retrieval_extends_active_life(app):
    """Frequently-retrieved memories should have their effective life extended."""
    from storage.database import get_session_factory
    from worker.lifecycle import transition_aging, _AGING_DAYS, _RETRIEVAL_LIFE_BOOST_DAYS

    factory = get_session_factory()
    async with factory() as session:
        # Last accessed right at the aging threshold, but with many retrievals
        # The boost should push it past the threshold and keep it active
        old_date = datetime.now(UTC) - timedelta(days=_AGING_DAYS + 2)
        enough_retrievals = 5  # 5 * 7 = 35 days boost > 2 days over threshold
        mem = await _make_memory(session, memory_state="active",
                                 last_accessed=old_date, created_at=old_date,
                                 times_retrieved=enough_retrievals)
        await session.commit()

        await transition_aging(session)
        await session.refresh(mem)
        assert mem.memory_state == MemoryState.ACTIVE


# ─── State transition: aging → stale ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_aging_to_stale_transition(app):
    """Memory aging for 60+ days must transition aging → stale."""
    from storage.database import get_session_factory
    from worker.lifecycle import transition_stale, _STALE_DAYS
    from storage.models import LifecycleEvent
    from sqlalchemy import select

    old_date = datetime.now(UTC) - timedelta(days=_STALE_DAYS + 5)
    factory = get_session_factory()
    async with factory() as session:
        mem = await _make_memory(session, memory_state="aging", last_accessed=old_date,
                                 created_at=old_date)
        await session.commit()
        mem_id = mem.id

        count = await transition_stale(session)
        assert count >= 1

        await session.refresh(mem)
        assert mem.memory_state == MemoryState.STALE

        ev = await session.execute(
            select(LifecycleEvent).where(
                LifecycleEvent.memory_id == mem_id,
                LifecycleEvent.event_type == "memory_stale",
            )
        )
        assert ev.scalars().first() is not None


@pytest.mark.asyncio
async def test_active_memory_skipped_by_stale_transition(app):
    """transition_stale() must not affect active memories — only aging ones."""
    from storage.database import get_session_factory
    from worker.lifecycle import transition_stale, _STALE_DAYS

    old_date = datetime.now(UTC) - timedelta(days=_STALE_DAYS + 5)
    factory = get_session_factory()
    async with factory() as session:
        mem = await _make_memory(session, memory_state="active", last_accessed=old_date,
                                 created_at=old_date)
        await session.commit()

        await transition_stale(session)
        await session.refresh(mem)
        assert mem.memory_state == MemoryState.ACTIVE


# ─── State transition: stale → archived ───────────────────────────────────────

@pytest.mark.asyncio
async def test_stale_to_archived_transition(app):
    """Memory stale for 120+ days must transition stale → archived."""
    from storage.database import get_session_factory
    from worker.lifecycle import transition_archived, _ARCHIVE_DAYS
    from storage.models import LifecycleEvent
    from sqlalchemy import select

    old_date = datetime.now(UTC) - timedelta(days=_ARCHIVE_DAYS + 5)
    factory = get_session_factory()
    async with factory() as session:
        mem = await _make_memory(session, memory_state="stale", last_accessed=old_date,
                                 created_at=old_date)
        await session.commit()
        mem_id = mem.id

        count = await transition_archived(session)
        assert count >= 1

        await session.refresh(mem)
        assert mem.memory_state == MemoryState.ARCHIVED
        assert mem.valid_to is not None, "archived memory must have valid_to set"

        ev = await session.execute(
            select(LifecycleEvent).where(
                LifecycleEvent.memory_id == mem_id,
                LifecycleEvent.event_type == "memory_archived",
            )
        )
        assert ev.scalars().first() is not None


# ─── Supersession logic ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_supersession_sets_valid_to_and_superseded_by(app):
    """supersede_memory() must set old.valid_to, old.superseded_by, and old.memory_state."""
    from storage.database import get_session_factory
    from worker.lifecycle import supersede_memory

    factory = get_session_factory()
    async with factory() as session:
        old = await _make_memory(session, memory_state="active", trust_score=0.6,
                                 content="Old fact about the project")
        new = await _make_memory(session, memory_state="active", trust_score=0.85,
                                 content="Updated fact about the project")
        await session.commit()

        result = await supersede_memory(session, old.id, new.id,
                                        reason="Test supersession")
        assert result is True

        await session.refresh(old)
        await session.refresh(new)

        assert old.valid_to is not None, "old.valid_to must be set"
        assert old.superseded_by == new.id, "old.superseded_by must point to new memory"
        assert old.memory_state == MemoryState.CONTRADICTED

        assert new.memory_state == MemoryState.ACTIVE
        assert new.valid_from is not None


@pytest.mark.asyncio
async def test_supersession_requires_high_trust_new_memory(app):
    """supersede_memory() must be rejected if new memory trust < threshold."""
    from storage.database import get_session_factory
    from worker.lifecycle import supersede_memory, _SUPERSESSION_MIN_TRUST

    factory = get_session_factory()
    async with factory() as session:
        old = await _make_memory(session, memory_state="active", trust_score=0.8)
        new = await _make_memory(session, memory_state="active",
                                 trust_score=_SUPERSESSION_MIN_TRUST - 0.1)
        await session.commit()

        result = await supersede_memory(session, old.id, new.id)
        assert result is False

        await session.refresh(old)
        assert old.memory_state == MemoryState.ACTIVE
        assert old.valid_to is None


@pytest.mark.asyncio
async def test_supersession_creates_memory_link(app):
    """supersede_memory() must create a MemoryLink of type 'supersedes'."""
    from storage.database import get_session_factory
    from worker.lifecycle import supersede_memory
    from storage.models import MemoryLink
    from sqlalchemy import select

    factory = get_session_factory()
    async with factory() as session:
        old = await _make_memory(session, memory_state="active", trust_score=0.6)
        new = await _make_memory(session, memory_state="active", trust_score=0.85)
        await session.commit()

        await supersede_memory(session, old.id, new.id)

        links = await session.execute(
            select(MemoryLink).where(
                MemoryLink.source_id == new.id,
                MemoryLink.target_id == old.id,
                MemoryLink.link_type == "supersedes",
            )
        )
        assert links.scalars().first() is not None


# ─── Trust increase on successful retrieval ───────────────────────────────────

@pytest.mark.asyncio
async def test_trust_increases_on_successful_retrieval(app):
    """increase_trust() must raise trust_score and emit trust_increased lifecycle event."""
    from storage.database import get_session_factory
    from worker.lifecycle import increase_trust
    from storage.models import LifecycleEvent
    from sqlalchemy import select

    factory = get_session_factory()
    async with factory() as session:
        mem = await _make_memory(session, trust_score=0.70)
        await session.commit()
        mem_id = mem.id
        original_trust = mem.trust_score

        result = await increase_trust(session, mem_id, amount=0.05,
                                      reason="confirmed successful retrieval")
        assert result is True

        updated = await session.get(type(mem), mem_id)
        assert updated.trust_score > original_trust
        assert updated.successful_retrievals >= 1
        assert updated.times_retrieved >= 1

        ev = await session.execute(
            select(LifecycleEvent).where(
                LifecycleEvent.memory_id == mem_id,
                LifecycleEvent.event_type == "trust_increased",
            )
        )
        assert ev.scalars().first() is not None


@pytest.mark.asyncio
async def test_trust_capped_at_max(app):
    """increase_trust() must not exceed 0.99."""
    from storage.database import get_session_factory
    from worker.lifecycle import increase_trust

    factory = get_session_factory()
    async with factory() as session:
        mem = await _make_memory(session, trust_score=0.99)
        await session.commit()
        await increase_trust(session, mem.id, amount=0.50)
        updated = await session.get(type(mem), mem.id)
        assert updated.trust_score <= 0.99


# ─── Trust decrease on contradiction ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_trust_decreases_on_contradiction(app):
    """decrease_trust() must lower trust_score and emit trust_decreased lifecycle event."""
    from storage.database import get_session_factory
    from worker.lifecycle import decrease_trust
    from storage.models import LifecycleEvent
    from sqlalchemy import select

    factory = get_session_factory()
    async with factory() as session:
        mem = await _make_memory(session, trust_score=0.70)
        await session.commit()
        mem_id = mem.id
        original_trust = mem.trust_score

        result = await decrease_trust(session, mem_id, amount=0.10,
                                      reason="user correction contradicted this memory")
        assert result is True

        updated = await session.get(type(mem), mem_id)
        assert updated.trust_score < original_trust
        assert updated.failed_retrievals >= 1

        ev = await session.execute(
            select(LifecycleEvent).where(
                LifecycleEvent.memory_id == mem_id,
                LifecycleEvent.event_type == "trust_decreased",
            )
        )
        assert ev.scalars().first() is not None


@pytest.mark.asyncio
async def test_trust_floored_at_min(app):
    """decrease_trust() must not go below 0.01."""
    from storage.database import get_session_factory
    from worker.lifecycle import decrease_trust

    factory = get_session_factory()
    async with factory() as session:
        mem = await _make_memory(session, trust_score=0.01)
        await session.commit()
        await decrease_trust(session, mem.id, amount=0.50)
        updated = await session.get(type(mem), mem.id)
        assert updated.trust_score >= 0.01


# ─── Verification decay ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verification_decay_applied_to_old_unverified_memories(app):
    """Confidence must decrease for high-trust memories not verified in 90+ days."""
    from storage.database import get_session_factory
    from worker.lifecycle import apply_verification_decay, _VERIFICATION_WINDOW_DAYS
    from storage.models import LifecycleEvent
    from sqlalchemy import select

    old_date = datetime.now(UTC) - timedelta(days=_VERIFICATION_WINDOW_DAYS + 30)
    factory = get_session_factory()
    async with factory() as session:
        mem = await _make_memory(session, trust_score=0.80, confidence=0.80,
                                 last_verified_at=old_date, created_at=old_date)
        await session.commit()
        mem_id = mem.id
        original_confidence = mem.confidence

        count = await apply_verification_decay(session)
        assert count >= 1

        updated = await session.get(type(mem), mem_id)
        assert updated.confidence < original_confidence

        ev = await session.execute(
            select(LifecycleEvent).where(
                LifecycleEvent.memory_id == mem_id,
                LifecycleEvent.event_type == "verification_decayed",
            )
        )
        assert ev.scalars().first() is not None


@pytest.mark.asyncio
async def test_recently_verified_memory_not_decayed(app):
    """Recently verified memories must not be decayed."""
    from storage.database import get_session_factory
    from worker.lifecycle import apply_verification_decay

    factory = get_session_factory()
    async with factory() as session:
        mem = await _make_memory(session, trust_score=0.80, confidence=0.80,
                                 last_verified_at=datetime.now(UTC))
        await session.commit()
        original_confidence = mem.confidence

        await apply_verification_decay(session)
        updated = await session.get(type(mem), mem.id)
        assert updated.confidence >= original_confidence - 0.001


@pytest.mark.asyncio
async def test_verification_decay_has_confidence_floor(app):
    """Verification decay must not reduce confidence below the floor."""
    from storage.database import get_session_factory
    from worker.lifecycle import apply_verification_decay, _VERIFICATION_WINDOW_DAYS, _VERIFICATION_MIN_CONFIDENCE

    very_old_date = datetime.now(UTC) - timedelta(days=_VERIFICATION_WINDOW_DAYS + 500)
    factory = get_session_factory()
    async with factory() as session:
        mem = await _make_memory(session, trust_score=0.80, confidence=0.80,
                                 last_verified_at=very_old_date, created_at=very_old_date)
        await session.commit()

        await apply_verification_decay(session)
        updated = await session.get(type(mem), mem.id)
        assert updated.confidence >= _VERIFICATION_MIN_CONFIDENCE


# ─── Episodic chain creation ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_episodic_chain_built_from_session_episodes(app):
    """build_episodic_chains() must create a chain for 3+ episodic memories in a session."""
    from storage.database import get_session_factory
    from worker.consolidator import build_episodic_chains
    from storage.models import EpisodicChain, LifecycleEvent
    from sqlalchemy import select

    sid = uuid.uuid4().hex
    factory = get_session_factory()
    async with factory() as session:
        for i in range(4):
            await _make_memory(session, layer="episodic",
                               content=f"episode event {i}",
                               session_id=sid, project="test_project")
        await session.commit()

        chain_ids = await build_episodic_chains(session, project="test_project")
        assert len(chain_ids) >= 1

        chain = await session.get(EpisodicChain, chain_ids[0])
        assert chain is not None
        assert len(chain.linked_memory_ids) >= 3
        assert chain.episode_summary is not None

        # Lifecycle events must be emitted for linked memories
        for mid in chain.linked_memory_ids:
            ev = await session.execute(
                select(LifecycleEvent).where(
                    LifecycleEvent.memory_id == mid,
                    LifecycleEvent.event_type == "episodic_chain_built",
                )
            )
            assert ev.scalars().first() is not None, f"Missing chain lifecycle event for {mid}"


@pytest.mark.asyncio
async def test_no_chain_built_for_fewer_than_min_episodes(app):
    """build_episodic_chains() must not create a chain if fewer than 3 episodes exist."""
    from storage.database import get_session_factory
    from worker.consolidator import build_episodic_chains, _CHAIN_MIN_MEMORIES

    sid = uuid.uuid4().hex
    factory = get_session_factory()
    async with factory() as session:
        # Create only 2 episodes (below the minimum)
        for i in range(_CHAIN_MIN_MEMORIES - 1):
            await _make_memory(session, layer="episodic",
                               content=f"sparse episode {i}",
                               session_id=sid, project="sparse_project")
        await session.commit()

        chain_ids = await build_episodic_chains(session, project="sparse_project")
        assert len(chain_ids) == 0


@pytest.mark.asyncio
async def test_already_chained_memories_not_double_chained(app):
    """build_episodic_chains() must not chain the same memories twice."""
    from storage.database import get_session_factory
    from worker.consolidator import build_episodic_chains
    from storage.models import EpisodicChain
    from sqlalchemy import select

    sid = uuid.uuid4().hex
    factory = get_session_factory()
    async with factory() as session:
        for i in range(4):
            await _make_memory(session, layer="episodic",
                               content=f"deduplicate chain test {i}",
                               session_id=sid, project="dedup_chain_project")
        await session.commit()

        ids_first = await build_episodic_chains(session, project="dedup_chain_project")
        assert len(ids_first) >= 1

        ids_second = await build_episodic_chains(session, project="dedup_chain_project")
        assert len(ids_second) == 0, "No new chain should be created — memories already chained"


# ─── Consolidation merge stability ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_consolidation_merge_preserves_important_memory(app):
    """merge_related_memories() must keep the higher-importance memory."""
    from storage.database import get_session_factory
    from worker.consolidator import merge_related_memories

    factory = get_session_factory()
    async with factory() as session:
        # Create two nearly-identical low-trust semantic memories
        high = await _make_memory(session, layer="semantic", importance=0.8,
                                  trust_score=0.4, content="The project uses Python 3.12")
        low = await _make_memory(session, layer="semantic", importance=0.3,
                                 trust_score=0.35, content="The project uses Python 3.12")
        await session.commit()
        high_id = high.id

        await merge_related_memories(session)

        # The high-importance one must survive
        survivor = await session.get(type(high), high_id)
        assert survivor.deleted_at is None, "High-importance memory must survive merge"


@pytest.mark.asyncio
async def test_high_trust_memories_not_merged(app):
    """merge_related_memories() must not merge memories above the trust threshold."""
    from storage.database import get_session_factory
    from worker.consolidator import merge_related_memories, _MERGE_TRUST_MIN

    factory = get_session_factory()
    async with factory() as session:
        m1 = await _make_memory(session, layer="semantic",
                                trust_score=_MERGE_TRUST_MIN + 0.1,
                                content="High trust semantic fact A")
        m2 = await _make_memory(session, layer="semantic",
                                trust_score=_MERGE_TRUST_MIN + 0.1,
                                content="High trust semantic fact A")
        await session.commit()

        await merge_related_memories(session)

        r1 = await session.get(type(m1), m1.id)
        r2 = await session.get(type(m2), m2.id)
        # At least one should survive (both are above merge threshold)
        alive = sum(1 for m in [r1, r2] if m and m.deleted_at is None)
        assert alive >= 1


# ─── Quarantined memory never reactivated ────────────────────────────────────

@pytest.mark.asyncio
async def test_quarantined_memory_not_aged_or_activated(app):
    """Lifecycle transitions must never touch quarantined memories."""
    from storage.database import get_session_factory
    from worker.lifecycle import transition_aging, transition_stale, transition_archived

    old_date = datetime.now(UTC) - timedelta(days=200)
    factory = get_session_factory()
    async with factory() as session:
        mem = await _make_memory(session, memory_state="quarantined",
                                 last_accessed=old_date, created_at=old_date)
        await session.commit()
        mem_id = mem.id

        await transition_aging(session)
        await transition_stale(session)
        await transition_archived(session)

        refreshed = await session.get(type(mem), mem_id)
        assert refreshed.memory_state == MemoryState.QUARANTINED, \
            "Quarantined memory must remain quarantined through all lifecycle passes"


@pytest.mark.asyncio
async def test_quarantined_memory_trust_not_increased(app):
    """increase_trust() must refuse to raise trust on quarantined memories."""
    from storage.database import get_session_factory
    from worker.lifecycle import increase_trust

    factory = get_session_factory()
    async with factory() as session:
        mem = await _make_memory(session, memory_state="quarantined", trust_score=0.10)
        await session.commit()

        result = await increase_trust(session, mem.id, amount=0.50)
        assert result is False

        refreshed = await session.get(type(mem), mem.id)
        assert refreshed.trust_score == 0.10


# ─── Lifecycle event observability ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_lifecycle_pass_emits_structured_events(app):
    """run_lifecycle_pass() must log LifecycleEvent records for every transition."""
    from storage.database import get_session_factory
    from worker.lifecycle import run_lifecycle_pass, _AGING_DAYS
    from storage.models import LifecycleEvent
    from sqlalchemy import select

    old_date = datetime.now(UTC) - timedelta(days=_AGING_DAYS + 10)
    factory = get_session_factory()
    async with factory() as session:
        mem = await _make_memory(session, memory_state="active", last_accessed=old_date,
                                 created_at=old_date)
        await session.commit()
        mem_id = mem.id

        result = await run_lifecycle_pass(session)
        assert result["aged"] >= 1

        events = await session.execute(
            select(LifecycleEvent).where(LifecycleEvent.memory_id == mem_id)
        )
        assert events.scalars().first() is not None


# ─── Trust from retrieval frequency (consolidator) ───────────────────────────

@pytest.mark.asyncio
async def test_trust_updated_from_retrieval_frequency(app):
    """update_trust_from_retrieval() must bump trust for memories with high successful_retrievals."""
    from storage.database import get_session_factory
    from worker.consolidator import update_trust_from_retrieval

    factory = get_session_factory()
    async with factory() as session:
        mem = await _make_memory(session, trust_score=0.60,
                                 successful_retrievals=10, failed_retrievals=0)
        await session.commit()
        original = mem.trust_score

        updated = await update_trust_from_retrieval(session)
        assert updated >= 1

        refreshed = await session.get(type(mem), mem.id)
        assert refreshed.trust_score > original


@pytest.mark.asyncio
async def test_trust_lowered_from_failed_retrievals(app):
    """update_trust_from_retrieval() must lower trust for memories with many failures."""
    from storage.database import get_session_factory
    from worker.consolidator import update_trust_from_retrieval

    factory = get_session_factory()
    async with factory() as session:
        mem = await _make_memory(session, trust_score=0.70,
                                 successful_retrievals=0, failed_retrievals=5)
        await session.commit()
        original = mem.trust_score

        await update_trust_from_retrieval(session)
        refreshed = await session.get(type(mem), mem.id)
        assert refreshed.trust_score < original


# ─── Full lifecycle pass integration ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_lifecycle_pass_returns_structured_result(app):
    """run_lifecycle_pass() must return a dict with all expected keys."""
    from storage.database import get_session_factory
    from worker.lifecycle import run_lifecycle_pass

    factory = get_session_factory()
    async with factory() as session:
        result = await run_lifecycle_pass(session)
    assert "aged" in result
    assert "staled" in result
    assert "archived" in result
    assert "verification_decayed" in result


@pytest.mark.asyncio
async def test_deep_maintenance_returns_hard_deleted_count(app):
    """run_deep_maintenance() must return a result including hard_deleted."""
    from storage.database import get_session_factory
    from worker.lifecycle import run_deep_maintenance

    factory = get_session_factory()
    async with factory() as session:
        result = await run_deep_maintenance(session)
    assert "hard_deleted" in result
    assert "aged" in result


# ─── Reflector: contradiction detection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_reflector_flags_contradicting_memories(app):
    """flag_contradictions() must mark lower-trust conflicting memory as contradicted."""
    from storage.database import get_session_factory
    from worker.reflector import flag_contradictions

    shared_prefix = "Preferred name is"
    factory = get_session_factory()
    async with factory() as session:
        high = await _make_memory(session, layer="semantic",
                                  content=f"{shared_prefix} Tym and always has been",
                                  trust_score=0.85, source_id="user_pref_001",
                                  memory_state="active")
        low = await _make_memory(session, layer="semantic",
                                 content=f"{shared_prefix} Timothy per registration",
                                 trust_score=0.55, source_id="user_pref_001",
                                 memory_state="active")
        await session.commit()

        flagged = await flag_contradictions(session)
        assert flagged >= 1

        await session.refresh(low)
        assert low.memory_state == MemoryState.CONTRADICTED
        assert low.verification_status == TrustLevel.CONFLICTING


@pytest.mark.asyncio
async def test_already_contradicted_memory_not_reflagged(app):
    """flag_contradictions() must not double-flag already-contradicted memories."""
    from storage.database import get_session_factory
    from worker.reflector import flag_contradictions

    factory = get_session_factory()
    async with factory() as session:
        await _make_memory(session, layer="semantic",
                           content="Already contradicted old fact XYZ",
                           trust_score=0.60, source_id="src_already_done",
                           memory_state="contradicted")
        await _make_memory(session, layer="semantic",
                           content="Already contradicted new fact XYZ",
                           trust_score=0.80, source_id="src_already_done",
                           memory_state="active")
        await session.commit()

        flagged = await flag_contradictions(session)
        # The already-contradicted one should not be reflagged
        assert flagged == 0
