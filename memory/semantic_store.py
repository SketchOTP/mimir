"""Semantic memory: durable facts, preferences, user rules, project knowledge."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, UTC
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import Memory, MemoryEvent, MemoryLink
from storage import vector_store
from memory.trust import MemoryState, TrustLevel, trust_defaults
from memory.memory_extractor import is_identity_statement
from memory.quarantine_detector import (
    QuarantineResult,
    apply_quarantine_overrides,
    check as quarantine_check,
    merge_quarantine_meta,
)


LAYER = "semantic"


_CONFLICT_SIGNALS = [
    # (negation_substring, optional_anchor_in_existing)
    # anchor="" means the negation alone is sufficient evidence of conflict
    ("not ", "call me"),
    ("don't", ""),          # "don't X" directly negates
    ("never", ""),          # "never X"
    ("actually", ""),       # "actually, ..." is a correction signal
    ("wrong", ""),
    ("incorrect", ""),
    ("instead of", "call me"),
]

# Similarity threshold above which we treat two memories as covering the same topic
_CONFLICT_TOPIC_THRESHOLD = 0.75
# Above this threshold they are duplicates, not conflicts
_DUPLICATE_THRESHOLD = 0.95
_TOPIC_STOPWORDS = {
    "a", "an", "and", "always", "am", "are", "as", "at", "be", "call", "do", "for",
    "give", "i", "if", "in", "is", "it", "lots", "me", "my", "never", "not", "of",
    "on", "or", "please", "prefer", "responses", "should", "the", "to", "use", "with",
}


def _looks_like_conflict(existing_content: str, new_content: str) -> bool:
    """Heuristic: does new_content seem to contradict existing_content?"""
    new_lower = new_content.lower()
    existing_lower = existing_content.lower()
    for neg, anchor in _CONFLICT_SIGNALS:
        if neg in new_lower and (not anchor or anchor in existing_lower):
            return True
    return False


def _topic_terms(text: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9]{4,}", text.lower())
        if token not in _TOPIC_STOPWORDS
    }


def _shares_topic(existing_content: str, new_content: str) -> bool:
    existing_terms = _topic_terms(existing_content)
    new_terms = _topic_terms(new_content)
    return bool(existing_terms and new_terms and existing_terms.intersection(new_terms))


def _is_high_trust_identity_memory(mem: Memory) -> bool:
    return (
        mem.layer == LAYER
        and mem.memory_state == MemoryState.ACTIVE
        and mem.verification_status == TrustLevel.TRUSTED_USER_EXPLICIT
        and (mem.trust_score or 0.0) >= 0.85
        and is_identity_statement(mem.content)
    )


async def store(
    session: AsyncSession,
    content: str,
    *,
    project: str | None = None,
    user_id: str | None = None,
    importance: float = 0.7,
    meta: dict | None = None,
    check_duplicates: bool = True,
    detect_conflicts: bool = True,
    # Trust / provenance
    source_type: str | None = None,
    verification_status: str | None = None,
    trust_score: float | None = None,
    confidence: float | None = None,
    created_by: str | None = None,
) -> Memory:
    # Quarantine check runs before anything else — poisoned content is never deduped
    q_result = quarantine_check(content)

    if check_duplicates and not q_result.quarantined:
        existing = await find_similar(session, content, project=project, threshold=_DUPLICATE_THRESHOLD)
        if existing:
            return existing[0]

    # Determine trust defaults from source_type if not explicitly provided
    _vs, _ts, _conf = trust_defaults(source_type or "system_observed")
    v_status = verification_status or _vs
    t_score = trust_score if trust_score is not None else _ts
    conf = confidence if confidence is not None else _conf

    # Apply quarantine override if triggered
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

    conflict_with: str | None = None

    if detect_conflicts and not q_result.quarantined:
        near = await find_similar(session, content, project=project, threshold=_CONFLICT_TOPIC_THRESHOLD)
        if not near:
            fallback_query = (
                select(Memory)
                .where(
                    Memory.layer == LAYER,
                    Memory.deleted_at.is_(None),
                    Memory.memory_state.notin_([MemoryState.ARCHIVED, MemoryState.DELETED]),
                )
                .order_by(Memory.created_at.desc())
                .limit(50)
            )
            if project:
                fallback_query = fallback_query.where(Memory.project == project)
            if user_id:
                fallback_query = fallback_query.where(Memory.user_id == user_id)
            fallback_result = await session.execute(fallback_query)
            near = [
                candidate for candidate in fallback_result.scalars()
                if candidate.content != content and _shares_topic(candidate.content, content)
            ]
        for candidate in near:
            if _looks_like_conflict(candidate.content, content):
                conflict_with = candidate.id
                if is_identity_statement(content) and _is_high_trust_identity_memory(candidate):
                    q_result = QuarantineResult(
                        quarantined=True,
                        flags=["high_trust_identity_contradiction"],
                        reason="Contradicts a high-trust identity memory",
                    )
                    state = MemoryState.QUARANTINED
                    poisoning_flags = q_result.flags
                    v_status, t_score, conf = apply_quarantine_overrides(
                        verification_status=v_status,
                        trust_score=t_score,
                        confidence=conf,
                        result=q_result,
                    )
                    meta = merge_quarantine_meta(
                        {
                            **(meta or {}),
                            "conflict_with": candidate.id,
                            "conflict_status": "unresolved",
                        },
                        q_result,
                    )
                else:
                    meta = {**(meta or {}), "conflict_with": candidate.id, "conflict_status": "unresolved"}
                    # Conflicting memory is less trustworthy until resolved
                    state = MemoryState.CONTRADICTED
                    v_status = TrustLevel.CONFLICTING
                    t_score = min(t_score, 0.4)
                    conf = min(conf, 0.5)
                break

    now = datetime.now(UTC)
    mem_id = f"sm_{uuid.uuid4().hex[:16]}"
    mem = Memory(
        id=mem_id,
        layer=LAYER,
        content=content,
        project=project,
        user_id=user_id,
        importance=importance,
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
        detail={"conflict": bool(conflict_with), "quarantined": bool(poisoning_flags)},
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
        source_type=source_type,
        metadata=meta,
    )

    if conflict_with:
        lnk = MemoryLink(
            id=uuid.uuid4().hex,
            source_id=mem_id,
            target_id=conflict_with,
            link_type="contradicts",
            strength=0.9,
        )
        session.add(lnk)
        await session.commit()

    return mem


async def get_conflicts(
    session: AsyncSession,
    project: str | None = None,
    limit: int = 50,
) -> list[Memory]:
    """Return memories that have an unresolved conflict flag."""
    q = (
        select(Memory)
        .where(
            Memory.layer == LAYER,
            Memory.deleted_at.is_(None),
            Memory.meta["conflict_status"].astext == "unresolved",
        )
        .order_by(Memory.created_at.desc())
        .limit(limit)
    )
    if project:
        q = q.where(Memory.project == project)
    result = await session.execute(q)
    return list(result.scalars())


async def get(session: AsyncSession, memory_id: str) -> Memory | None:
    result = await session.get(Memory, memory_id)
    if result and result.layer == LAYER and not result.deleted_at:
        return result
    return None


async def update_content(session: AsyncSession, memory_id: str, content: str) -> Memory | None:
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
    # Only an explicit admin action (not currently exposed) can clear quarantine.
    session.add(MemoryEvent(id=uuid.uuid4().hex, memory_id=memory_id, event_type="updated"))
    await session.commit()
    vector_store.upsert(
        LAYER, memory_id, content,
        user_id=mem.user_id,
        project_id=mem.project,
        importance=mem.importance,
        trust_score=mem.trust_score or 0.7,
        verification_status=mem.verification_status or TrustLevel.TRUSTED_SYSTEM_OBSERVED,
        memory_state=mem.memory_state or MemoryState.ACTIVE,
        source_type=mem.source_type,
        metadata=mem.meta,
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


async def find_similar(
    session: AsyncSession,
    query: str,
    project: str | None = None,
    user_id: str | None = None,
    threshold: float = 0.8,
    limit: int = 10,
) -> list[Memory]:
    where = {"project_id": {"$eq": project}} if project else None
    hits = vector_store.search(LAYER, query, n_results=limit, where=where, user_id=user_id)
    ids = [h["id"] for h in hits if h["score"] >= threshold]
    if not ids:
        return []
    q = select(Memory).where(Memory.id.in_(ids), Memory.deleted_at.is_(None))
    result = await session.execute(q)
    mems = {m.id: m for m in result.scalars()}
    return [mems[i] for i in ids if i in mems]


async def link(
    session: AsyncSession,
    source_id: str,
    target_id: str,
    link_type: str = "related",
    strength: float = 1.0,
) -> MemoryLink:
    lnk = MemoryLink(
        id=uuid.uuid4().hex,
        source_id=source_id,
        target_id=target_id,
        link_type=link_type,
        strength=strength,
    )
    session.add(lnk)
    await session.commit()
    return lnk
