"""Lifecycle worker — memory state machine, supersession, and verification decay.

Responsibility:
  - Age active memories toward stale → archived based on:
      recency, retrieval frequency, verification age, contradiction count, trust score
  - Apply temporal supersession when a new high-trust memory contradicts an old active one
  - Apply verification decay: unverified high-trust memories lose confidence over time
  - Cleanup deleted memories older than retention window

State machine:
    active → aging → stale → archived
    active/aging → contradicted  (via reflector)
    quarantined stays quarantined (never reactivated automatically)

Runs nightly (aging + decay + archival) plus a weekly deep-maintenance pass.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, UTC
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import Memory, MemoryLink, LifecycleEvent
from memory.trust import MemoryState

logger = logging.getLogger(__name__)

# ── Aging thresholds ──────────────────────────────────────────────────────────
# Days since last_accessed (or created_at if never accessed) before state transition
_AGING_DAYS = 30           # active → aging
_STALE_DAYS = 60           # aging → stale
_ARCHIVE_DAYS = 120        # stale → archived
_DELETE_RETENTION_DAYS = 180  # hard-delete memories soft-deleted > this long

# Retrieval frequency boost: each retrieval extends effective "life" by this many days
_RETRIEVAL_LIFE_BOOST_DAYS = 7

# Verification decay: confidence reduction per day past verification window
_VERIFICATION_DECAY_PER_DAY = 0.003   # 0.3% per day
_VERIFICATION_WINDOW_DAYS = 90        # grace period before decay starts
_VERIFICATION_MIN_CONFIDENCE = 0.30   # floor for decay

# Supersession: minimum trust of the new memory to supersede an old one
_SUPERSESSION_MIN_TRUST = 0.75


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _as_utc(dt: datetime) -> datetime:
    """Return a UTC-aware datetime, normalizing naive datetimes from SQLite."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _effective_last_active(mem: Memory) -> datetime:
    """Return the effective last-active datetime, boosted by retrieval count."""
    base = _as_utc(mem.last_accessed or mem.created_at or datetime.now(UTC))
    boost = timedelta(days=_RETRIEVAL_LIFE_BOOST_DAYS * (mem.times_retrieved or 0))
    return base + boost


def _log_transition(
    session: AsyncSession,
    mem: Memory,
    event_type: str,
    from_state: str,
    to_state: str,
    reason: str,
    meta: dict | None = None,
) -> None:
    session.add(LifecycleEvent(
        id=uuid.uuid4().hex,
        memory_id=mem.id,
        event_type=event_type,
        from_state=from_state,
        to_state=to_state,
        trust_before=mem.trust_score,
        trust_after=mem.trust_score,
        reason=reason,
        meta=meta,
    ))


# ─── State transitions ────────────────────────────────────────────────────────

async def transition_aging(session: AsyncSession) -> int:
    """Transition active → aging for memories past the aging threshold.

    Quarantined memories are never touched.
    Returns count of memories transitioned.
    """
    now = datetime.now(UTC)
    result = await session.execute(
        select(Memory).where(
            Memory.memory_state == MemoryState.ACTIVE,
            Memory.deleted_at.is_(None),
        )
    )
    mems = result.scalars().all()
    count = 0

    for mem in mems:
        effective = _effective_last_active(mem)
        if (now - effective).days >= _AGING_DAYS:
            _log_transition(session, mem, "memory_aged", MemoryState.ACTIVE, MemoryState.AGING,
                            f"No activity for {(now - effective).days}d (threshold={_AGING_DAYS}d)")
            mem.memory_state = MemoryState.AGING
            session.add(mem)
            count += 1

    if count:
        await session.commit()
    logger.info("lifecycle: %d memories transitioned active→aging", count)
    return count


async def transition_stale(session: AsyncSession) -> int:
    """Transition aging → stale for memories past the stale threshold.

    Returns count of memories transitioned.
    """
    now = datetime.now(UTC)
    result = await session.execute(
        select(Memory).where(
            Memory.memory_state == MemoryState.AGING,
            Memory.deleted_at.is_(None),
        )
    )
    mems = result.scalars().all()
    count = 0

    for mem in mems:
        effective = _effective_last_active(mem)
        if (now - effective).days >= _STALE_DAYS:
            _log_transition(session, mem, "memory_stale", MemoryState.AGING, MemoryState.STALE,
                            f"No activity for {(now - effective).days}d (threshold={_STALE_DAYS}d)")
            mem.memory_state = MemoryState.STALE
            session.add(mem)
            count += 1

    if count:
        await session.commit()
    logger.info("lifecycle: %d memories transitioned aging→stale", count)
    return count


async def transition_archived(session: AsyncSession) -> int:
    """Transition stale → archived for memories past the archive threshold.

    Returns count of memories transitioned.
    """
    now = datetime.now(UTC)
    result = await session.execute(
        select(Memory).where(
            Memory.memory_state == MemoryState.STALE,
            Memory.deleted_at.is_(None),
        )
    )
    mems = result.scalars().all()
    count = 0

    for mem in mems:
        effective = _effective_last_active(mem)
        if (now - effective).days >= _ARCHIVE_DAYS:
            _log_transition(session, mem, "memory_archived", MemoryState.STALE, MemoryState.ARCHIVED,
                            f"No activity for {(now - effective).days}d (threshold={_ARCHIVE_DAYS}d)")
            mem.memory_state = MemoryState.ARCHIVED
            mem.valid_to = now
            session.add(mem)
            count += 1

    if count:
        await session.commit()
    logger.info("lifecycle: %d memories transitioned stale→archived", count)
    return count


# ─── Supersession ─────────────────────────────────────────────────────────────

async def supersede_memory(
    session: AsyncSession,
    old_memory_id: str,
    new_memory_id: str,
    reason: str = "Superseded by newer high-trust memory",
) -> bool:
    """Apply temporal supersession: old → contradicted, new → active.

    Sets old.valid_to=now, old.superseded_by=new.id, old.memory_state=contradicted.
    Sets new.valid_from=now, new.memory_state=active.

    Returns True if supersession was applied.
    """
    old = await session.get(Memory, old_memory_id)
    new = await session.get(Memory, new_memory_id)

    if old is None or new is None:
        logger.warning("lifecycle: supersede called with missing memory ids")
        return False

    if new.trust_score < _SUPERSESSION_MIN_TRUST:
        logger.debug(
            "lifecycle: supersession skipped — new memory trust %.2f < %.2f",
            new.trust_score, _SUPERSESSION_MIN_TRUST,
        )
        return False

    now = datetime.now(UTC)

    old.valid_to = now
    old.superseded_by = new.id
    old.memory_state = MemoryState.CONTRADICTED
    session.add(old)

    new.valid_from = now
    new.memory_state = MemoryState.ACTIVE
    session.add(new)

    # Create a MemoryLink: new supersedes old
    session.add(MemoryLink(
        id=uuid.uuid4().hex,
        source_id=new.id,
        target_id=old.id,
        link_type="supersedes",
        strength=1.0,
    ))

    session.add(LifecycleEvent(
        id=uuid.uuid4().hex,
        memory_id=old.id,
        event_type="memory_superseded",
        from_state=MemoryState.ACTIVE,
        to_state=MemoryState.CONTRADICTED,
        trust_before=old.trust_score,
        trust_after=old.trust_score,
        reason=reason,
        meta={"superseded_by": new.id},
    ))

    await session.commit()
    logger.info("lifecycle: memory %s superseded by %s", old.id, new.id)
    return True


# ─── Verification decay ───────────────────────────────────────────────────────

async def apply_verification_decay(session: AsyncSession) -> int:
    """Decay confidence for high-trust memories not verified recently.

    Memories whose last_verified_at is None use created_at as the reference.
    Returns count of memories whose confidence was reduced.
    """
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=_VERIFICATION_WINDOW_DAYS)

    result = await session.execute(
        select(Memory).where(
            Memory.deleted_at.is_(None),
            Memory.memory_state.notin_(list(MemoryState.BLOCKED)),
            Memory.trust_score >= 0.6,
        )
    )
    mems = result.scalars().all()
    decayed = 0

    for mem in mems:
        reference = mem.last_verified_at or mem.created_at
        if reference is None or reference.replace(tzinfo=UTC) > cutoff:
            continue

        days_unverified = (now - reference.replace(tzinfo=UTC)).days - _VERIFICATION_WINDOW_DAYS
        if days_unverified <= 0:
            continue

        decay_amount = _VERIFICATION_DECAY_PER_DAY * days_unverified
        old_confidence = mem.confidence
        new_confidence = max(_VERIFICATION_MIN_CONFIDENCE, old_confidence - decay_amount)

        if abs(new_confidence - old_confidence) < 0.001:
            continue

        mem.confidence = new_confidence
        session.add(mem)
        session.add(LifecycleEvent(
            id=uuid.uuid4().hex,
            memory_id=mem.id,
            event_type="verification_decayed",
            trust_before=mem.trust_score,
            trust_after=mem.trust_score,
            reason=f"Unverified for {days_unverified + _VERIFICATION_WINDOW_DAYS}d; "
                   f"confidence {old_confidence:.3f} → {new_confidence:.3f}",
            meta={"days_unverified": days_unverified + _VERIFICATION_WINDOW_DAYS},
        ))
        decayed += 1

    if decayed:
        await session.commit()
    logger.info("lifecycle: verification decay applied to %d memories", decayed)
    return decayed


# ─── Cleanup ──────────────────────────────────────────────────────────────────

async def cleanup_deleted(session: AsyncSession) -> int:
    """Hard-delete memories that have been soft-deleted longer than retention window.

    Only removes: layer=episodic, trust_score < 0.6, past retention window.
    Semantic and procedural memories with higher trust are preserved.
    Returns count of hard-deleted rows.
    """
    from storage import vector_store

    cutoff = datetime.now(UTC) - timedelta(days=_DELETE_RETENTION_DAYS)
    result = await session.execute(
        select(Memory).where(
            Memory.deleted_at.is_not(None),
            Memory.deleted_at < cutoff,
            Memory.trust_score < 0.6,
            Memory.layer == "episodic",
        )
    )
    mems = result.scalars().all()
    count = 0
    for mem in mems:
        try:
            vector_store.delete(mem.layer, mem.id)
        except Exception:
            pass
        await session.delete(mem)
        count += 1

    if count:
        await session.commit()
    logger.info("lifecycle: hard-deleted %d expired episodic memories", count)
    return count


# ─── Trust updates ────────────────────────────────────────────────────────────

async def increase_trust(
    session: AsyncSession,
    memory_id: str,
    amount: float = 0.05,
    reason: str = "successful retrieval",
) -> bool:
    """Increase trust score for a memory after a successful retrieval or confirmation."""
    mem = await session.get(Memory, memory_id)
    if mem is None or mem.deleted_at:
        return False
    # Quarantined memories cannot have trust increased
    if mem.memory_state == MemoryState.QUARANTINED:
        return False
    old = mem.trust_score
    mem.trust_score = min(0.99, old + amount)
    mem.successful_retrievals = (mem.successful_retrievals or 0) + 1
    mem.times_retrieved = (mem.times_retrieved or 0) + 1
    mem.last_retrieved_at = datetime.now(UTC)
    session.add(mem)
    session.add(LifecycleEvent(
        id=uuid.uuid4().hex,
        memory_id=mem.id,
        event_type="trust_increased",
        trust_before=old,
        trust_after=mem.trust_score,
        reason=reason,
    ))
    await session.commit()
    return True


async def decrease_trust(
    session: AsyncSession,
    memory_id: str,
    amount: float = 0.10,
    reason: str = "contradiction or correction",
) -> bool:
    """Decrease trust score for a memory after a contradiction or user correction."""
    mem = await session.get(Memory, memory_id)
    if mem is None or mem.deleted_at:
        return False
    old = mem.trust_score
    mem.trust_score = max(0.01, old - amount)
    mem.failed_retrievals = (mem.failed_retrievals or 0) + 1
    session.add(mem)
    session.add(LifecycleEvent(
        id=uuid.uuid4().hex,
        memory_id=mem.id,
        event_type="trust_decreased",
        trust_before=old,
        trust_after=mem.trust_score,
        reason=reason,
    ))
    await session.commit()
    return True


# ─── Full lifecycle pass ──────────────────────────────────────────────────────

async def run_lifecycle_pass(session: AsyncSession) -> dict[str, Any]:
    """Nightly lifecycle pass: aging + stale + archive + verification decay."""
    aged = await transition_aging(session)
    staled = await transition_stale(session)
    archived = await transition_archived(session)
    decayed = await apply_verification_decay(session)
    return {
        "aged": aged,
        "staled": staled,
        "archived": archived,
        "verification_decayed": decayed,
    }


async def run_deep_maintenance(session: AsyncSession) -> dict[str, Any]:
    """Weekly deep maintenance: lifecycle pass + cleanup of hard-deleted records."""
    nightly = await run_lifecycle_pass(session)
    cleaned = await cleanup_deleted(session)
    return {**nightly, "hard_deleted": cleaned}
