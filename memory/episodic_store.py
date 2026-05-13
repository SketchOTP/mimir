"""Episodic memory: events, conversations, task traces, outcomes."""

from __future__ import annotations

import uuid
from datetime import datetime, UTC
from typing import Any

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


LAYER = "episodic"


async def store(
    session: AsyncSession,
    content: str,
    *,
    project: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    importance: float = 0.5,
    expires_at: datetime | None = None,
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
    mem_id = f"ep_{uuid.uuid4().hex[:16]}"
    mem = Memory(
        id=mem_id,
        layer=LAYER,
        content=content,
        project=project,
        user_id=user_id,
        session_id=session_id,
        importance=importance,
        expires_at=expires_at,
        meta=meta,
        # Temporal
        valid_from=now,
        memory_state=state,
        # Trust
        trust_score=t_score,
        source_type=source_type,
        created_by=created_by or user_id,
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
        user_id=user_id,
        project_id=project,
        importance=importance,
        created_at=mem.created_at.isoformat() if mem.created_at else None,
        trust_score=t_score,
        verification_status=v_status,
        memory_state=state,
        metadata={"session_id": session_id or ""},
    )
    return mem


async def get(session: AsyncSession, memory_id: str) -> Memory | None:
    result = await session.get(Memory, memory_id)
    if result and result.layer == LAYER and not result.deleted_at:
        await _touch(session, result)
    return result


async def list_recent(
    session: AsyncSession,
    project: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
) -> list[Memory]:
    q = select(Memory).where(Memory.layer == LAYER, Memory.deleted_at.is_(None))
    if project:
        q = q.where(Memory.project == project)
    if session_id:
        q = q.where(Memory.session_id == session_id)
    q = q.order_by(Memory.created_at.desc()).limit(limit)
    result = await session.execute(q)
    return list(result.scalars())


async def delete(session: AsyncSession, memory_id: str) -> bool:
    mem = await session.get(Memory, memory_id)
    if not mem or mem.layer != LAYER:
        return False
    mem.deleted_at = datetime.now(UTC)
    mem.memory_state = MemoryState.DELETED
    session.add(MemoryEvent(id=uuid.uuid4().hex, memory_id=memory_id, event_type="deleted"))
    await session.commit()
    vector_store.delete(LAYER, memory_id)
    return True


async def _touch(session: AsyncSession, mem: Memory) -> None:
    mem.access_count += 1
    mem.last_accessed = datetime.now(UTC)
    await session.commit()


async def update_content(session: AsyncSession, memory_id: str, content: str) -> Memory | None:
    mem = await session.get(Memory, memory_id)
    if not mem or mem.layer != LAYER or mem.deleted_at:
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
        user_id=mem.user_id,
        project_id=mem.project,
        importance=mem.importance,
        created_at=mem.created_at.isoformat() if mem.created_at else None,
        trust_score=mem.trust_score or 0.7,
        verification_status=mem.verification_status or TrustLevel.TRUSTED_SYSTEM_OBSERVED,
        memory_state=mem.memory_state or MemoryState.ACTIVE,
        metadata={"session_id": mem.session_id or ""},
    )
    return mem
