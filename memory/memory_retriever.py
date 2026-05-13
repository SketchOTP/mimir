"""Cross-layer memory retrieval with trust-state filtering and relevance scoring."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import Memory
from storage import vector_store
from memory.trust import MemoryState
from mimir.config import get_settings

# States that must never surface in any recall path
_RECALL_BLOCKED = list(MemoryState.BLOCKED)           # quarantined, archived, deleted
# States additionally excluded from identity/high-priority context
_IDENTITY_BLOCKED = list(MemoryState.HIGH_PRIORITY_EXCLUDED)  # + stale, contradicted


async def search(
    session: AsyncSession,
    query: str,
    *,
    layers: list[str] | None = None,
    project: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    limit: int | None = None,
    min_score: float = 0.3,
) -> list[dict[str, Any]]:
    """
    Search across memory layers and return ranked results.
    Returns list of {memory, score, layer}.

    Quarantined, archived, and deleted memories are never returned.
    user_id is always applied as a vector-level isolation filter.
    """
    settings = get_settings()
    n = limit or settings.max_memories_per_context

    where: dict | None = None
    if project:
        where = {"project_id": {"$eq": project}}

    hits = vector_store.search(
        None if not layers else layers[0], query,
        n_results=n * 2, where=where, user_id=user_id,
    )
    if layers and len(layers) > 1:
        for lyr in layers[1:]:
            hits += vector_store.search(lyr, query, n_results=n, where=where, user_id=user_id)
        hits.sort(key=lambda x: x["score"], reverse=True)

    hits = [h for h in hits if h["score"] >= min_score][:n]

    if not hits:
        return []

    ids = [h["id"] for h in hits]
    score_map = {h["id"]: h["score"] for h in hits}
    layer_map = {h["id"]: h["layer"] for h in hits}

    q = (
        select(Memory)
        .where(
            Memory.id.in_(ids),
            Memory.deleted_at.is_(None),
            Memory.memory_state.notin_(_RECALL_BLOCKED),
        )
    )
    result = await session.execute(q)
    mems = {m.id: m for m in result.scalars()}

    out = []
    for mem_id in ids:
        if mem_id in mems:
            out.append(
                {
                    "memory": mems[mem_id],
                    "score": score_map[mem_id],
                    "layer": layer_map[mem_id],
                }
            )

    return out


async def get_identity_context(
    session: AsyncSession, user_id: str | None = None, project: str | None = None
) -> list[Memory]:
    """Return high-importance, active semantic memories (name, user rules, preferences).

    Stale, contradicted, quarantined, archived, and deleted memories are excluded —
    only `active` memories are allowed in the identity context.
    """
    q = (
        select(Memory)
        .where(
            Memory.layer == "semantic",
            Memory.deleted_at.is_(None),
            Memory.importance >= 0.8,
            Memory.memory_state == MemoryState.ACTIVE,
        )
        .order_by(Memory.importance.desc())
        .limit(10)
    )
    if project:
        q = q.where(Memory.project == project)
    if user_id:
        q = q.where(or_(Memory.user_id == user_id, Memory.user_id.is_(None)))
    result = await session.execute(q)
    return list(result.scalars())


async def get_session_memories(
    session: AsyncSession, session_id: str, limit: int = 20
) -> list[Memory]:
    q = (
        select(Memory)
        .where(
            Memory.session_id == session_id,
            Memory.deleted_at.is_(None),
            Memory.memory_state.notin_(_RECALL_BLOCKED),
        )
        .order_by(Memory.created_at.asc())
        .limit(limit)
    )
    result = await session.execute(q)
    return list(result.scalars())
