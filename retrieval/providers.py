"""Independent retrieval providers for the P6 orchestration layer.

Each provider returns a list[ProviderHit].  Providers are intentionally
independent — they share no state and can run concurrently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from memory.trust import MemoryState
from retrieval.bootstrap_capsules import capsule_query_score, load_bootstrap_capsules
from storage import vector_store
from storage.models import Memory

_BLOCKED = list(MemoryState.BLOCKED)


@dataclass
class ProviderHit:
    memory_id: str
    score: float
    retrieval_source: str
    trust_score: float
    memory_state: str
    created_at: datetime | None
    reason: str


# ─── Vector provider ──────────────────────────────────────────────────────────

async def vector_provider(
    session: AsyncSession,
    query: str,
    *,
    project: str | None = None,
    user_id: str | None = None,
    limit: int = 20,
    min_score: float = 0.25,
) -> list[ProviderHit]:
    where = {"project_id": {"$eq": project}} if project else None
    raw = vector_store.search(None, query, n_results=limit, where=where, user_id=user_id)
    raw = [h for h in raw if h["score"] >= min_score]
    if not raw:
        return []

    ids = [h["id"] for h in raw]
    score_map = {h["id"]: h["score"] for h in raw}

    result = await session.execute(
        select(Memory).where(
            Memory.id.in_(ids),
            Memory.deleted_at.is_(None),
            Memory.memory_state.notin_(_BLOCKED),
        )
    )
    mems = {m.id: m for m in result.scalars()}

    return [
        ProviderHit(
            memory_id=mid,
            score=score_map[mid],
            retrieval_source="vector",
            trust_score=mems[mid].trust_score or 0.5,
            memory_state=mems[mid].memory_state or MemoryState.ACTIVE,
            created_at=mems[mid].created_at,
            reason="vector_similarity",
        )
        for mid in ids
        if mid in mems
    ]


# ─── Keyword / FTS5 provider ─────────────────────────────────────────────────

async def keyword_provider(
    session: AsyncSession,
    query: str,
    *,
    project: str | None = None,
    user_id: str | None = None,
    limit: int = 20,
) -> list[ProviderHit]:
    """Keyword retrieval using the active search backend (FTS5/tsvector/LIKE)."""
    from storage.search_backend import get_search_backend

    backend = get_search_backend()
    hits = await backend.search(
        session, query,
        user_id=user_id,
        project_id=project,
        limit=limit * 2,
    )

    if hits:
        ids = [h.memory_id for h in hits]
        score_map = {h.memory_id: h.score for h in hits}
        backend_name = type(backend).__name__

        # SQL post-filter as defense-in-depth — user/project isolation at DB level
        q = select(Memory).where(
            Memory.id.in_(ids),
            Memory.deleted_at.is_(None),
            Memory.memory_state.notin_(_BLOCKED),
        )
        if project:
            q = q.where(Memory.project == project)
        if user_id:
            q = q.where(or_(Memory.user_id == user_id, Memory.user_id.is_(None)))

        result = await session.execute(q)
        mems = {m.id: m for m in result.scalars()}

        return [
            ProviderHit(
                memory_id=mid,
                score=score_map.get(mid, 0.1),
                retrieval_source="keyword",
                trust_score=mems[mid].trust_score or 0.5,
                memory_state=mems[mid].memory_state or MemoryState.ACTIVE,
                created_at=mems[mid].created_at,
                reason=backend_name,
            )
            for mid in ids
            if mid in mems
        ][:limit]

    # ── LIKE fallback when backend returned nothing ───────────────────────────
    words = [w.strip() for w in query.lower().split() if len(w.strip()) > 2]
    if not words:
        return []

    q = select(Memory).where(
        Memory.deleted_at.is_(None),
        Memory.memory_state.notin_(_BLOCKED),
        or_(*[Memory.content.ilike(f"%{w}%") for w in words[:5]]),
    )
    if project:
        q = q.where(Memory.project == project)
    if user_id:
        q = q.where(or_(Memory.user_id == user_id, Memory.user_id.is_(None)))
    q = q.limit(limit * 3)

    result = await session.execute(q)
    mems = list(result.scalars())

    scored: list[tuple[Memory, float]] = []
    for mem in mems:
        content_lower = mem.content.lower()
        matched = sum(1 for w in words if w in content_lower)
        score = matched / len(words)
        if score > 0:
            scored.append((mem, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [
        ProviderHit(
            memory_id=mem.id,
            score=score,
            retrieval_source="keyword",
            trust_score=mem.trust_score or 0.5,
            memory_state=mem.memory_state or MemoryState.ACTIVE,
            created_at=mem.created_at,
            reason="keyword_like_fallback",
        )
        for mem, score in scored[:limit]
    ]


async def bootstrap_capsule_provider(
    session: AsyncSession,
    query: str,
    *,
    project: str | None = None,
    user_id: str | None = None,
    limit: int = 20,
) -> list[ProviderHit]:
    """Direct SQL fallback for project bootstrap capsules."""
    mems = await load_bootstrap_capsules(
        session,
        project=project,
        query=query,
        user_id=user_id,
        limit=limit,
    )
    return [
        ProviderHit(
            memory_id=mem.id,
            score=capsule_query_score(mem.meta if isinstance(mem.meta, dict) else None, query),
            retrieval_source="bootstrap_capsule",
            trust_score=mem.trust_score or 0.5,
            memory_state=mem.memory_state or MemoryState.ACTIVE,
            created_at=mem.created_at,
            reason=f"bootstrap_sql:{(mem.meta or {}).get('capsule_type', '')}",
        )
        for mem in mems
    ]


# ─── Identity provider ────────────────────────────────────────────────────────

async def identity_provider(
    session: AsyncSession,
    *,
    project: str | None = None,
    user_id: str | None = None,
    limit: int = 10,
) -> list[ProviderHit]:
    q = (
        select(Memory)
        .where(
            Memory.layer == "semantic",
            Memory.deleted_at.is_(None),
            Memory.importance >= 0.8,
            Memory.memory_state == MemoryState.ACTIVE,
        )
        .order_by(Memory.importance.desc())
        .limit(limit)
    )
    if project:
        q = q.where(Memory.project == project)
    if user_id:
        q = q.where(or_(Memory.user_id == user_id, Memory.user_id.is_(None)))

    result = await session.execute(q)
    mems = list(result.scalars())
    return [
        ProviderHit(
            memory_id=mem.id,
            score=mem.importance,
            retrieval_source="identity",
            trust_score=mem.trust_score or 0.5,
            memory_state=mem.memory_state or MemoryState.ACTIVE,
            created_at=mem.created_at,
            reason="identity_high_importance",
        )
        for mem in mems
    ]


# ─── Episodic recent provider ─────────────────────────────────────────────────

async def episodic_recent_provider(
    session: AsyncSession,
    *,
    project: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    limit: int = 10,
) -> list[ProviderHit]:
    q = (
        select(Memory)
        .where(
            Memory.layer == "episodic",
            Memory.deleted_at.is_(None),
            Memory.memory_state.notin_(_BLOCKED),
        )
        .order_by(Memory.created_at.desc())
        .limit(limit)
    )
    if project:
        q = q.where(Memory.project == project)
    if user_id:
        q = q.where(or_(Memory.user_id == user_id, Memory.user_id.is_(None)))
    if session_id:
        q = q.where(Memory.session_id == session_id)

    result = await session.execute(q)
    mems = list(result.scalars())

    now = datetime.utcnow()
    return [
        ProviderHit(
            memory_id=mem.id,
            score=max(0.0, 1.0 - (now - mem.created_at).total_seconds() / (7 * 86400))
            if mem.created_at
            else 0.5,
            retrieval_source="episodic_recent",
            trust_score=mem.trust_score or 0.5,
            memory_state=mem.memory_state or MemoryState.ACTIVE,
            created_at=mem.created_at,
            reason="episodic_recency",
        )
        for mem in mems
    ]


# ─── Procedural provider ──────────────────────────────────────────────────────

# Only surface procedural memories with enough trust to be actionable
_PROCEDURAL_MIN_TRUST = 0.60


async def procedural_provider(
    session: AsyncSession,
    *,
    project: str | None = None,
    limit: int = 10,
    min_trust: float = _PROCEDURAL_MIN_TRUST,
) -> list[ProviderHit]:
    """Return high-confidence procedural memories.

    Filters by min_trust (proxy for confidence) so only validated procedural
    knowledge surfaces.  Ordered by trust DESC so the most-confirmed rules
    appear first.
    """
    q = (
        select(Memory)
        .where(
            Memory.layer == "procedural",
            Memory.deleted_at.is_(None),
            Memory.memory_state.notin_(_BLOCKED),
            Memory.trust_score >= min_trust,
        )
        .order_by(Memory.trust_score.desc(), Memory.importance.desc())
        .limit(limit)
    )
    if project:
        q = q.where(Memory.project == project)

    result = await session.execute(q)
    mems = list(result.scalars())
    return [
        ProviderHit(
            memory_id=mem.id,
            score=(mem.trust_score or 0.5) * (mem.importance or 0.5),
            retrieval_source="procedural",
            trust_score=mem.trust_score or 0.5,
            memory_state=mem.memory_state or MemoryState.ACTIVE,
            created_at=mem.created_at,
            reason=f"procedural trust={mem.trust_score:.2f} evidence={mem.evidence_count or 0}",
        )
        for mem in mems
    ]


# ─── Simulation evidence provider ────────────────────────────────────────────

async def simulation_provider(
    session: AsyncSession,
    query: str,
    *,
    project: str | None = None,
    limit: int = 5,
) -> list[ProviderHit]:
    """Return semantic Memory rows backed by historical simulation evidence.

    Memories are created by simulation.historical_memory.store_simulation_memory
    when a simulation run completes.  This provider surfaces them during
    planning-related retrieval so operators can see prior forecasts.
    """
    from simulation.historical_memory import get_simulation_context

    keywords = [w for w in query.lower().split() if len(w) > 2]
    hits = await get_simulation_context(session, keywords, project=project, limit=limit)
    if not hits:
        return []

    ids = [h["id"] for h in hits]
    score_map = {h["id"]: h["score"] for h in hits}

    result = await session.execute(
        select(Memory).where(
            Memory.id.in_(ids),
            Memory.deleted_at.is_(None),
            Memory.memory_state.notin_(_BLOCKED),
        )
    )
    mems = {m.id: m for m in result.scalars()}

    return [
        ProviderHit(
            memory_id=mid,
            score=score_map.get(mid, 0.4),
            retrieval_source="simulation",
            trust_score=mems[mid].trust_score or 0.6,
            memory_state=mems[mid].memory_state or MemoryState.ACTIVE,
            created_at=mems[mid].created_at,
            reason="historical_simulation_evidence",
        )
        for mid in ids
        if mid in mems
    ]


# ─── High-trust semantic provider ────────────────────────────────────────────

async def high_trust_provider(
    session: AsyncSession,
    query: str,
    *,
    project: str | None = None,
    user_id: str | None = None,
    min_trust: float = 0.7,
    limit: int = 15,
) -> list[ProviderHit]:
    where = {"project_id": {"$eq": project}} if project else None
    raw = vector_store.search("semantic", query, n_results=limit * 2, where=where, user_id=user_id)
    if not raw:
        return []

    ids = [h["id"] for h in raw]
    score_map = {h["id"]: h["score"] for h in raw}

    result = await session.execute(
        select(Memory).where(
            Memory.id.in_(ids),
            Memory.deleted_at.is_(None),
            Memory.memory_state.notin_(_BLOCKED),
            Memory.trust_score >= min_trust,
        )
    )
    mems = {m.id: m for m in result.scalars()}

    return [
        ProviderHit(
            memory_id=mid,
            score=score_map[mid],
            retrieval_source="high_trust",
            trust_score=mems[mid].trust_score or 0.5,
            memory_state=mems[mid].memory_state or MemoryState.ACTIVE,
            created_at=mems[mid].created_at,
            reason=f"high_trust_semantic trust={mems[mid].trust_score:.2f}",
        )
        for mid in ids
        if mid in mems
    ][:limit]
