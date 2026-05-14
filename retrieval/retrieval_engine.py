"""High-level retrieval API: semantic search with logging."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from memory import memory_retriever
from storage.models import RetrievalLog


def _capsule_type(meta: dict[str, Any] | None) -> str | None:
    if not isinstance(meta, dict):
        return None
    return meta.get("capsule_type") or meta.get("bootstrap_type")


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
    hits = await memory_retriever.search(
        session,
        query,
        layers=layers,
        project=project,
        session_id=session_id,
        user_id=user_id,
        limit=limit,
        min_score=min_score,
    )

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
            "capsule_type": _capsule_type(h["memory"].meta),
        }
        for h in hits
    ]
