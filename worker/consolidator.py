"""Consolidator worker — the offline "dreaming" layer.

Responsibility:
  - Deduplicate memories (extends memory_consolidator)
  - Compress episodic sequences into episodic chains
  - Merge related memories across sessions
  - Update trust scores based on retrieval frequency
  - Build procedural lessons from episodic chains

Runs nightly. Conservative — never silently deletes high-trust memories.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, UTC

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import Memory, MemoryEvent, EpisodicChain, LifecycleEvent
from memory.trust import MemoryState

logger = logging.getLogger(__name__)

# Trust score adjustments
_TRUST_BUMP_PER_RETRIEVAL = 0.02      # each successful retrieval raises trust
_TRUST_DROP_PER_FAILURE = 0.05        # each failed retrieval lowers trust
_TRUST_MAX = 0.99
_TRUST_MIN = 0.01

# Thresholds for chain building
_CHAIN_MIN_MEMORIES = 3               # minimum episodes to form a chain
_CHAIN_WINDOW_HOURS = 48              # look-back window for episode grouping
_CHAIN_MAX_MEMORIES = 20              # cap per chain

# Deduplication — delegate to memory_consolidator; this adds trust-aware merge
_MERGE_TRUST_MIN = 0.5                # only merge memories with enough trust
_MERGE_IMPORTANCE_MIN = 0.3


async def update_trust_from_retrieval(session: AsyncSession) -> int:
    """Adjust trust scores based on accumulated retrieval frequency data.

    Memories retrieved many times successfully → trust bumped.
    Memories associated with failures → trust dropped.
    Returns count of memories updated.
    """
    result = await session.execute(
        select(Memory).where(
            Memory.deleted_at.is_(None),
            Memory.memory_state.notin_(list(MemoryState.BLOCKED)),
        )
    )
    mems = result.scalars().all()
    updated = 0

    for mem in mems:
        old_trust = mem.trust_score
        new_trust = old_trust

        # Successful retrievals bump trust
        if mem.successful_retrievals > 0:
            bump = _TRUST_BUMP_PER_RETRIEVAL * mem.successful_retrievals
            new_trust = min(_TRUST_MAX, new_trust + bump)

        # Failed retrievals (corrections) lower trust
        if mem.failed_retrievals > 0:
            drop = _TRUST_DROP_PER_FAILURE * mem.failed_retrievals
            new_trust = max(_TRUST_MIN, new_trust - drop)

        if abs(new_trust - old_trust) < 0.001:
            continue

        event_type = "trust_increased" if new_trust > old_trust else "trust_decreased"
        mem.trust_score = new_trust
        session.add(mem)
        session.add(LifecycleEvent(
            id=uuid.uuid4().hex,
            memory_id=mem.id,
            event_type=event_type,
            trust_before=old_trust,
            trust_after=new_trust,
            reason=f"retrieval_frequency: successful={mem.successful_retrievals}, failed={mem.failed_retrievals}",
        ))
        updated += 1

    if updated:
        await session.commit()
    logger.info("consolidator: trust updated for %d memories", updated)
    return updated


async def build_episodic_chains(
    session: AsyncSession,
    project: str | None = None,
    user_id: str | None = None,
) -> list[str]:
    """Group recent episodic memories into narrative chains.

    Chains are built by grouping episodic memories from the same session or
    the same project within a time window. Only creates a chain when 3+ episodes
    are available and no chain already covers those memories.

    Returns list of created EpisodicChain IDs.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=_CHAIN_WINDOW_HOURS)
    filters = [
        Memory.layer == "episodic",
        Memory.deleted_at.is_(None),
        Memory.created_at >= cutoff,
        Memory.memory_state.notin_(list(MemoryState.BLOCKED)),
    ]
    if project:
        filters.append(Memory.project == project)

    result = await session.execute(
        select(Memory).where(*filters).order_by(Memory.created_at)
    )
    episodes = result.scalars().all()

    # Group by session_id; fall back to project bucket when session_id is None
    groups: dict[str, list[Memory]] = {}
    for ep in episodes:
        key = ep.session_id or (f"project:{ep.project}" if ep.project else "global")
        groups.setdefault(key, []).append(ep)

    # Load existing chains to avoid re-chaining already-linked memories
    existing_result = await session.execute(select(EpisodicChain))
    existing_chains = existing_result.scalars().all()
    already_linked: set[str] = set()
    for chain in existing_chains:
        already_linked.update(chain.linked_memory_ids or [])

    created_ids: list[str] = []
    for key, group in groups.items():
        # Filter out already-chained memories
        new_mems = [m for m in group if m.id not in already_linked]
        if len(new_mems) < _CHAIN_MIN_MEMORIES:
            continue

        # Cap to max chain size (most recent first)
        chain_mems = new_mems[-_CHAIN_MAX_MEMORIES:]
        ids = [m.id for m in chain_mems]

        # Build a brief summary from memory contents
        snippets = [m.content[:80] for m in chain_mems[:5]]
        summary = " → ".join(snippets)
        if len(chain_mems) > 5:
            summary += f" … (+{len(chain_mems) - 5} more)"

        chain = EpisodicChain(
            id=uuid.uuid4().hex,
            title=f"Episode: {key[:60]}",
            episode_summary=summary,
            episode_type="session" if key.startswith("project:") is False and key != "global" else "project",
            linked_memory_ids=ids,
            project=project,
            user_id=user_id,
        )
        session.add(chain)
        # Emit lifecycle events for linked memories
        for mid in ids:
            session.add(LifecycleEvent(
                id=uuid.uuid4().hex,
                memory_id=mid,
                event_type="episodic_chain_built",
                reason=f"Linked into chain {chain.id}",
                meta={"chain_id": chain.id},
            ))
        created_ids.append(chain.id)
        already_linked.update(ids)

    if created_ids:
        await session.commit()
    logger.info("consolidator: built %d episodic chains", len(created_ids))
    return created_ids


async def merge_related_memories(
    session: AsyncSession,
    project: str | None = None,
) -> int:
    """Merge semantically similar low-trust semantic memories into a stronger one.

    Differs from deduplication: dedup uses vector similarity; this merges by
    combining content into a single richer memory when both are below the trust
    threshold for independent survival.

    Returns count of merges performed.
    """
    from storage import vector_store

    filters = [
        Memory.layer == "semantic",
        Memory.deleted_at.is_(None),
        Memory.trust_score < _MERGE_TRUST_MIN,
        Memory.importance >= _MERGE_IMPORTANCE_MIN,
        Memory.memory_state == MemoryState.ACTIVE,
    ]
    if project:
        filters.append(Memory.project == project)

    result = await session.execute(select(Memory).where(*filters))
    candidates = list(result.scalars().all())
    merged = 0
    processed: set[str] = set()

    for mem in candidates:
        if mem.id in processed:
            continue

        # Find similar memories using vector search
        try:
            hits = vector_store.search("semantic", mem.content, n_results=4)
        except Exception:
            continue

        for hit in hits:
            if hit["id"] == mem.id or hit["id"] in processed:
                continue
            if hit["score"] < 0.90:
                continue

            other = await session.get(Memory, hit["id"])
            if (
                other is None
                or other.deleted_at
                or other.trust_score >= _MERGE_TRUST_MIN
                or other.memory_state != MemoryState.ACTIVE
            ):
                continue

            # Merge: combine content into the higher-importance one
            keeper = mem if mem.importance >= other.importance else other
            loser = other if keeper is mem else mem

            keeper.content = f"{keeper.content}\n\nAlso known: {loser.content}"
            keeper.importance = min(1.0, keeper.importance + 0.05)
            keeper.trust_score = min(0.9, (keeper.trust_score + loser.trust_score) / 2 + 0.05)
            loser.deleted_at = datetime.now(UTC)
            session.add(keeper)
            session.add(loser)
            session.add(MemoryEvent(
                id=uuid.uuid4().hex,
                memory_id=loser.id,
                event_type="consolidated",
                detail={"reason": "merge_related", "kept": keeper.id},
            ))
            session.add(LifecycleEvent(
                id=uuid.uuid4().hex,
                memory_id=loser.id,
                event_type="consolidation_merge",
                from_state=loser.memory_state,
                to_state="deleted",
                reason=f"Merged into {keeper.id}",
            ))
            try:
                vector_store.delete("semantic", loser.id)
            except Exception:
                pass

            processed.add(loser.id)
            merged += 1
            break  # one merge per candidate per pass

        processed.add(mem.id)

    if merged:
        await session.commit()
    logger.info("consolidator: merged %d related memory pairs", merged)
    return merged


async def write_chain_lesson(
    session: AsyncSession,
    chain_id: str,
    lesson: str,
) -> bool:
    """Set the procedural_lesson field on an existing EpisodicChain.

    Returns True if the chain was found and updated.
    """
    chain = await session.get(EpisodicChain, chain_id)
    if not chain:
        return False
    chain.procedural_lesson = lesson
    session.add(chain)
    await session.commit()
    return True


async def run_consolidation_pass(
    session: AsyncSession,
    project: str | None = None,
    user_id: str | None = None,
) -> dict:
    """Full consolidator pass: trust update + chain building + merge + dedup + procedural promotion + inference."""
    from memory.memory_consolidator import prune_stale, deduplicate_semantic
    from worker.procedural_promoter import promote_procedural_lessons
    from worker.feedback_inference import infer_retrieval_outcomes

    trust_updated = await update_trust_from_retrieval(session)
    chains = await build_episodic_chains(session, project=project, user_id=user_id)
    merged = await merge_related_memories(session, project=project)
    pruned = await prune_stale(session)
    deduped = await deduplicate_semantic(session, project=project)
    promoted = await promote_procedural_lessons(session, project=project, user_id=user_id)
    inference = await infer_retrieval_outcomes(session)

    return {
        "trust_updated": trust_updated,
        "chains_built": len(chains),
        "chain_ids": chains,
        "merged": merged,
        "pruned": pruned,
        "deduped": deduped,
        "procedural_promoted": promoted,
        "inference_sessions": inference["sessions_processed"],
        "inference_positive": inference["positive_inferred"],
        "inference_negative": inference["negative_inferred"],
    }
