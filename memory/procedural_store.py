"""Procedural memory: skills, workflows, behavior rules, learned procedures."""

from __future__ import annotations

import uuid
from datetime import datetime, UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import Memory, MemoryEvent
from storage import vector_store
from memory.trust import MemoryState, TrustLevel, trust_defaults
from memory.quarantine_detector import (
    apply_quarantine_overrides,
    check as quarantine_check,
    merge_quarantine_meta,
)


LAYER = "procedural"


async def store(
    session: AsyncSession,
    content: str,
    *,
    project: str | None = None,
    importance: float = 0.8,
    meta: dict | None = None,
    # Trust / provenance
    source_type: str | None = None,
    verification_status: str | None = None,
    trust_score: float | None = None,
    confidence: float | None = None,
    created_by: str | None = None,
) -> Memory:
    _vs, _ts, _conf = trust_defaults(source_type or "system_observed")
    v_status = verification_status or _vs
    t_score = trust_score if trust_score is not None else _ts
    conf = confidence if confidence is not None else _conf

    q_result = quarantine_check(content)
    state = MemoryState.ACTIVE
    poisoning_flags: list | None = None
    if q_result.quarantined:
        state = MemoryState.QUARANTINED
        v_status, t_score, conf = apply_quarantine_overrides(
            verification_status=v_status,
            trust_score=t_score,
            confidence=conf,
            result=q_result,
        )
        poisoning_flags = q_result.flags
        meta = merge_quarantine_meta(meta, q_result)

    now = datetime.now(UTC)
    mem_id = f"pr_{uuid.uuid4().hex[:16]}"
    mem = Memory(
        id=mem_id,
        layer=LAYER,
        content=content,
        project=project,
        importance=importance,
        meta=meta,
        # Temporal
        valid_from=now,
        memory_state=state,
        # Trust
        trust_score=t_score,
        source_type=source_type,
        created_by=created_by,
        verification_status=v_status,
        confidence=conf,
        poisoning_flags=poisoning_flags,
    )
    session.add(mem)
    session.add(MemoryEvent(
        id=uuid.uuid4().hex, memory_id=mem_id, event_type="created",
        detail={"quarantined": bool(poisoning_flags)},
    ))
    await session.commit()
    vector_store.upsert(
        LAYER, mem_id, content,
        project_id=project,
        importance=importance,
        created_at=mem.created_at.isoformat() if mem.created_at else None,
        trust_score=t_score,
        verification_status=v_status,
        memory_state=state,
    )
    return mem


async def get(session: AsyncSession, memory_id: str) -> Memory | None:
    result = await session.get(Memory, memory_id)
    if result and result.layer == LAYER and not result.deleted_at:
        return result
    return None


async def list_for_project(
    session: AsyncSession, project: str | None = None, limit: int = 100
) -> list[Memory]:
    q = select(Memory).where(Memory.layer == LAYER, Memory.deleted_at.is_(None))
    if project:
        q = q.where(Memory.project == project)
    q = q.order_by(Memory.importance.desc()).limit(limit)
    result = await session.execute(q)
    return list(result.scalars())


async def update(session: AsyncSession, memory_id: str, content: str) -> Memory | None:
    mem = await get(session, memory_id)
    if not mem:
        return None
    q_result = quarantine_check(content)
    mem.content = content
    mem.updated_at = datetime.now(UTC)
    mem.poisoning_flags = q_result.flags or None
    mem.meta = merge_quarantine_meta(mem.meta, q_result)
    if q_result.quarantined:
        mem.memory_state = MemoryState.QUARANTINED
        mem.verification_status, mem.trust_score, mem.confidence = apply_quarantine_overrides(
            verification_status=mem.verification_status or TrustLevel.TRUSTED_SYSTEM_OBSERVED,
            trust_score=mem.trust_score or 0.7,
            confidence=mem.confidence or 0.7,
            result=q_result,
        )
    # Quarantined memories cannot be reactivated via content update — state is sticky.
    session.add(MemoryEvent(id=uuid.uuid4().hex, memory_id=memory_id, event_type="updated"))
    await session.commit()
    vector_store.upsert(
        LAYER, memory_id, content,
        project_id=mem.project,
        importance=mem.importance,
        trust_score=mem.trust_score or 0.7,
        verification_status=mem.verification_status or TrustLevel.TRUSTED_SYSTEM_OBSERVED,
        memory_state=mem.memory_state or MemoryState.ACTIVE,
    )
    return mem


async def delete(session: AsyncSession, memory_id: str) -> bool:
    mem = await get(session, memory_id)
    if not mem:
        return False
    mem.deleted_at = datetime.now(UTC)
    mem.memory_state = MemoryState.DELETED
    session.add(MemoryEvent(id=uuid.uuid4().hex, memory_id=memory_id, event_type="deleted"))
    await session.commit()
    vector_store.delete(LAYER, memory_id)
    return True


async def search(
    session: AsyncSession, query: str, project: str | None = None, user_id: str | None = None,
    limit: int = 10,
) -> list[Memory]:
    where = {"project_id": {"$eq": project}} if project else None
    hits = vector_store.search(LAYER, query, n_results=limit, where=where, user_id=user_id)
    ids = [h["id"] for h in hits]
    if not ids:
        return []
    q = select(Memory).where(Memory.id.in_(ids), Memory.deleted_at.is_(None))
    result = await session.execute(q)
    mems = {m.id: m for m in result.scalars()}
    return [mems[i] for i in ids if i in mems]


# Minimum trust required for a new procedural memory to supersede an existing one
_SUPERSESSION_MIN_TRUST = 0.75


async def supersede(
    session: AsyncSession,
    old_memory_id: str,
    new_memory_id: str,
) -> bool:
    """Mark old_memory as superseded by new_memory.

    Constraints:
    - Both memories must exist and be procedural
    - new_memory.trust_score must be >= _SUPERSESSION_MIN_TRUST
    - new_memory.trust_score must be >= old_memory.trust_score

    Sets old_memory.valid_to = now, superseded_by = new_id, memory_state = archived.
    Creates a MemoryLink(supersedes) and a LifecycleEvent.
    Returns True if supersession was applied.
    """
    from storage.models import MemoryLink, LifecycleEvent
    from memory.trust import MemoryState

    old = await get(session, old_memory_id)
    new = await get(session, new_memory_id)

    if not old or not new:
        return False
    if new.trust_score is None or new.trust_score < _SUPERSESSION_MIN_TRUST:
        return False
    if (new.trust_score or 0) < (old.trust_score or 0):
        return False
    if old.memory_state in {MemoryState.QUARANTINED, MemoryState.DELETED}:
        return False

    now = datetime.now(UTC)
    old_state = old.memory_state

    old.valid_to = now
    old.superseded_by = new_memory_id
    old.memory_state = MemoryState.ARCHIVED
    session.add(old)

    link = MemoryLink(
        id=uuid.uuid4().hex,
        source_id=new_memory_id,
        target_id=old_memory_id,
        link_type="supersedes",
        strength=new.trust_score or 1.0,
    )
    session.add(link)

    session.add(LifecycleEvent(
        id=uuid.uuid4().hex,
        memory_id=old_memory_id,
        event_type="memory_superseded",
        from_state=old_state,
        to_state=MemoryState.ARCHIVED,
        trust_before=old.trust_score,
        trust_after=old.trust_score,
        reason=f"Superseded by procedural memory {new_memory_id}",
        meta={"new_memory_id": new_memory_id},
    ))

    await session.commit()
    return True
