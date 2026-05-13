"""Consolidate episodic memories into semantic/procedural, prune stale memories."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, UTC

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import Memory, MemoryEvent
from storage import vector_store
from memory.memory_extractor import classify, extract_importance

logger = logging.getLogger(__name__)


async def prune_stale(
    session: AsyncSession,
    older_than_days: int = 90,
    min_importance: float = 0.3,
) -> int:
    """Soft-delete episodic memories that are old and unimportant."""
    cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
    q = select(Memory).where(
        Memory.layer == "episodic",
        Memory.deleted_at.is_(None),
        Memory.created_at < cutoff,
        Memory.importance <= min_importance,
        Memory.access_count <= 2,
    )
    result = await session.execute(q)
    mems = result.scalars().all()
    count = 0
    for mem in mems:
        mem.deleted_at = datetime.now(UTC)
        session.add(
            MemoryEvent(
                id=uuid.uuid4().hex,
                memory_id=mem.id,
                event_type="consolidated",
                detail={"reason": "stale_prune"},
            )
        )
        try:
            vector_store.delete(mem.layer, mem.id)
        except Exception:
            pass
        count += 1
    await session.commit()
    logger.info("Pruned %d stale episodic memories", count)
    return count


async def deduplicate_semantic(
    session: AsyncSession,
    threshold: float = 0.97,
    project: str | None = None,
) -> int:
    """Remove near-duplicate semantic memories, keeping the higher-importance one."""
    from storage.vector_store import search as vec_search  # local import avoids circular deps

    filters = [Memory.layer == "semantic", Memory.deleted_at.is_(None)]
    if project is not None:
        filters.append(Memory.project == project)
    q = select(Memory).where(*filters)
    result = await session.execute(q)
    mems = list(result.scalars())
    removed = set()
    count = 0

    for mem in mems:
        if mem.id in removed:
            continue
        hits = vec_search("semantic", mem.content, n_results=5)
        for hit in hits:
            if hit["id"] == mem.id or hit["id"] in removed:
                continue
            if hit["score"] >= threshold:
                # keep the one with higher importance
                other = await session.get(Memory, hit["id"])
                if other and not other.deleted_at:
                    loser = other if mem.importance >= other.importance else mem
                    loser.deleted_at = datetime.now(UTC)
                    session.add(
                        MemoryEvent(
                            id=uuid.uuid4().hex,
                            memory_id=loser.id,
                            event_type="consolidated",
                            detail={"reason": "deduplication", "kept": mem.id},
                        )
                    )
                    try:
                        vector_store.delete("semantic", loser.id)
                    except Exception:
                        pass
                    removed.add(loser.id)
                    count += 1

    if count:
        await session.commit()
    logger.info("Deduplicated %d semantic memories", count)
    return count


async def get_consolidation_stats(session: AsyncSession) -> dict:
    total = await session.execute(select(func.count(Memory.id)).where(Memory.deleted_at.is_(None)))
    by_layer = {}
    for layer in ["episodic", "semantic", "procedural", "working"]:
        cnt = await session.execute(
            select(func.count(Memory.id)).where(
                Memory.layer == layer, Memory.deleted_at.is_(None)
            )
        )
        by_layer[layer] = cnt.scalar_one()
    return {"total": total.scalar_one(), "by_layer": by_layer}
