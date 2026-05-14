"""High-level retrieval API: semantic search with logging."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from memory import memory_retriever
from memory.trust import MemoryState
from retrieval.bootstrap_capsules import (
    capsule_query_score,
    capsule_type,
    load_bootstrap_capsules,
)
from retrieval.providers import keyword_provider
from storage.models import Memory, RetrievalLog

_BLOCKED = list(MemoryState.BLOCKED)


def _query_variants(query: str) -> list[str]:
    variants: list[str] = []
    raw = query.strip()
    if raw:
        variants.append(raw)

    normalized = " ".join(query.lower().replace("_", " ").strip().split())
    if normalized and normalized not in variants:
        variants.append(normalized)

    underscored = normalized.replace(" ", "_")
    if underscored and underscored not in variants:
        variants.append(underscored)

    return variants

async def search(
    session: AsyncSession,
    query: str,
    *,
    layer: str | None = None,
    project: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    limit: int = 10,
    min_score: float = 0.3,
) -> list[dict[str, Any]]:
    layers = [layer] if layer else None
    n = max(1, limit)
    variants = _query_variants(query)

    candidates: dict[str, dict[str, Any]] = {}

    bootstrap_hits = await load_bootstrap_capsules(
        session,
        project=project,
        query=query,
        user_id=user_id,
        limit=n,
    )
    for mem in bootstrap_hits:
        candidates[mem.id] = {
            "memory": mem,
            "layer": mem.layer,
            "score": capsule_query_score(mem.meta if isinstance(mem.meta, dict) else None, query),
        }

    # 1) Vector retrieval across all variants.
    for variant in variants:
        vector_hits = await memory_retriever.search(
            session,
            variant,
            layers=layers,
            project=project,
            session_id=session_id,
            user_id=user_id,
            limit=n * 3,
            min_score=0.0,
        )
        for hit in vector_hits:
            mem = hit["memory"]
            mem_id = mem.id
            score = float(hit["score"])
            row = candidates.get(mem_id)
            if row is None:
                candidates[mem_id] = {"memory": mem, "layer": mem.layer, "score": score}
            elif score > row["score"]:
                row["score"] = score

    # 2) Keyword/FTS retrieval to catch label-style queries.
    keyword_scores: dict[str, float] = {}
    for variant in variants:
        kw_hits = await keyword_provider(
            session,
            variant,
            project=project,
            user_id=user_id,
            limit=n * 4,
        )
        for hit in kw_hits:
            score = float(hit.score)
            if score > keyword_scores.get(hit.memory_id, 0.0):
                keyword_scores[hit.memory_id] = score

    # 3) Load DB rows for keyword-only matches and merge scores.
    missing_ids = [mid for mid in keyword_scores if mid not in candidates]
    if missing_ids:
        q = select(Memory).where(
            Memory.id.in_(missing_ids),
            Memory.deleted_at.is_(None),
            Memory.memory_state.notin_(_BLOCKED),
        )
        if layer:
            q = q.where(Memory.layer == layer)
        if project:
            q = q.where(Memory.project == project)
        if user_id:
            q = q.where(or_(Memory.user_id == user_id, Memory.user_id.is_(None)))

        result = await session.execute(q)
        for mem in result.scalars():
            candidates[mem.id] = {
                "memory": mem,
                "layer": mem.layer,
                "score": keyword_scores.get(mem.id, 0.0),
            }

    for mem_id, kw_score in keyword_scores.items():
        if mem_id in candidates:
            candidates[mem_id]["score"] = max(candidates[mem_id]["score"], kw_score)

    # 4) Bootstrap capsule relevance boosts.
    reranked: list[dict[str, Any]] = []
    for entry in candidates.values():
        mem = entry["memory"]
        boosted = max(
            float(entry["score"]),
            capsule_query_score(mem.meta if isinstance(mem.meta, dict) else None, query),
        )
        reranked.append({"memory": mem, "layer": entry["layer"], "score": boosted})

    reranked.sort(key=lambda h: h["score"], reverse=True)
    hits = [h for h in reranked if h["score"] >= min_score][:n]

    log = RetrievalLog(
        id=f"ret_{uuid.uuid4().hex[:16]}",
        query=query,
        layer=layer,
        results_count=len(hits),
        top_score=hits[0]["score"] if hits else None,
        session_id=session_id,
        project=project,
    )
    session.add(log)
    await session.commit()

    return [
        {
            "id": h["memory"].id,
            "layer": h["layer"],
            "content": h["memory"].content,
            "score": h["score"],
            "importance": h["memory"].importance,
            "created_at": h["memory"].created_at.isoformat() if h["memory"].created_at else None,
            "project": h["memory"].project,
            "project_id": h["memory"].project,
            "source_type": h["memory"].source_type,
            "memory_state": h["memory"].memory_state,
            "verification_status": h["memory"].verification_status,
            "trust_score": h["memory"].trust_score,
            "meta": h["memory"].meta or {},
            "capsule_type": capsule_type(h["memory"].meta),
        }
        for h in hits
    ]
