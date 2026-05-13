"""Procedural lesson promoter — converts episodic patterns into procedural memory.

Responsibility:
  - Scan episodic chains for procedural_lesson fields
  - Group similar lessons across chains (by normalized text matching)
  - When the same lesson appears in >= MIN_LESSON_COUNT chains:
      * Compute a confidence score
      * If confidence >= AUTO_APPROVE_THRESHOLD: create an approval-gated ImprovementProposal
      * If confidence >= STORE_THRESHOLD and evidence >= MIN_EVIDENCE: store as procedural memory
        candidate (still requires approval before becoming active)
  - Update evidence_count and derived_from_episode_ids on promoted procedural memories
  - Never auto-promote without at least MIN_EVIDENCE confirming chains
  - Never promote low-confidence lessons

Runs as part of the nightly consolidation pass.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import Memory, EpisodicChain, ImprovementProposal, LifecycleEvent
from memory.trust import MemoryState, TrustLevel

logger = logging.getLogger(__name__)

# Minimum number of episodic chains that must share a lesson before promotion
_MIN_LESSON_COUNT = 2

# Minimum trust for the promoted procedural memory
_PROCEDURAL_BASE_TRUST = 0.65

# Confidence threshold to submit for approval (high-impact lessons)
_APPROVAL_THRESHOLD = 0.7

# Don't store below this confidence
_STORE_THRESHOLD = 0.6

# Base importance for procedurally-promoted memories
_PROCEDURAL_IMPORTANCE = 0.8


def _normalize_lesson(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace for fuzzy grouping."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _confidence_from_evidence(count: int) -> float:
    """Evidence-count → confidence. Asymptotic: saturates around 0.95."""
    return min(0.95, 0.5 + 0.15 * count)


async def _existing_procedural_for_lesson(
    session: AsyncSession, lesson_normalized: str, project: str | None
) -> Memory | None:
    """Return the first active procedural memory whose normalized content matches."""
    filters = [
        Memory.layer == "procedural",
        Memory.deleted_at.is_(None),
        Memory.memory_state.notin_(list(MemoryState.BLOCKED)),
    ]
    if project:
        filters.append(Memory.project == project)
    result = await session.execute(select(Memory).where(*filters))
    for mem in result.scalars():
        if _normalize_lesson(mem.content) == lesson_normalized:
            return mem
    return None


async def _proposal_exists_for_lesson(
    session: AsyncSession, lesson: str, project: str | None
) -> bool:
    q = select(ImprovementProposal).where(
        ImprovementProposal.improvement_type == "procedural_promotion",
        ImprovementProposal.status.in_(["proposed", "approved"]),
    )
    if project:
        q = q.where(ImprovementProposal.project == project)
    result = await session.execute(q)
    for prop in result.scalars():
        if _normalize_lesson(prop.reason) == _normalize_lesson(lesson):
            return True
    return False


async def promote_procedural_lessons(
    session: AsyncSession,
    project: str | None = None,
    user_id: str | None = None,
) -> dict:
    """Main entry point for procedural lesson promotion.

    Returns a dict with counts: lessons_scanned, candidates_found,
    memories_created, proposals_created, evidence_updated.
    """
    # Load all chains with a lesson set
    filters = [EpisodicChain.procedural_lesson.isnot(None)]
    if project:
        filters.append(EpisodicChain.project == project)
    result = await session.execute(select(EpisodicChain).where(*filters))
    chains = list(result.scalars())

    if not chains:
        return {
            "lessons_scanned": 0,
            "candidates_found": 0,
            "memories_created": 0,
            "proposals_created": 0,
            "evidence_updated": 0,
        }

    # Group chains by normalized lesson text
    groups: dict[str, list[EpisodicChain]] = {}
    for chain in chains:
        key = _normalize_lesson(chain.procedural_lesson or "")
        if key:
            groups.setdefault(key, []).append(chain)

    memories_created = 0
    proposals_created = 0
    evidence_updated = 0

    for norm_key, group_chains in groups.items():
        count = len(group_chains)
        if count < _MIN_LESSON_COUNT:
            continue

        # Use the most-common raw lesson text as the canonical form
        canonical = max(group_chains, key=lambda c: len(c.procedural_lesson or "")).procedural_lesson
        if not canonical:
            continue

        episode_ids = [c.id for c in group_chains]
        confidence = _confidence_from_evidence(count)

        # Check if we already have a procedural memory for this lesson
        existing = await _existing_procedural_for_lesson(session, norm_key, project)
        if existing:
            # Update evidence tracking on the existing memory
            existing.evidence_count = max(existing.evidence_count or 0, count)
            old_ids = list(existing.derived_from_episode_ids or [])
            merged_ids = list(set(old_ids + episode_ids))
            existing.derived_from_episode_ids = merged_ids
            # Gently raise trust as evidence accumulates
            old_trust = existing.trust_score or _PROCEDURAL_BASE_TRUST
            new_trust = min(0.95, old_trust + 0.01 * (count - (len(old_ids))))
            existing.trust_score = new_trust
            session.add(existing)
            if new_trust != old_trust:
                session.add(LifecycleEvent(
                    id=uuid.uuid4().hex,
                    memory_id=existing.id,
                    event_type="trust_increased",
                    trust_before=old_trust,
                    trust_after=new_trust,
                    reason=f"procedural_evidence_accumulation: count={count}",
                ))
            evidence_updated += 1
            continue

        if confidence < _STORE_THRESHOLD:
            continue

        # Create a procedural memory candidate (pending approval via proposal)
        # Only submit an approval proposal; don't directly activate the memory
        if confidence >= _APPROVAL_THRESHOLD:
            if await _proposal_exists_for_lesson(session, canonical, project):
                continue

            proposal = ImprovementProposal(
                id=uuid.uuid4().hex,
                improvement_type="procedural_promotion",
                title=f"Promote episodic lesson to procedural memory",
                reason=canonical,
                current_behavior=f"Lesson observed in {count} episodic chains but not formalized",
                proposed_behavior=f"Create procedural memory: {canonical[:200]}",
                risk="low",
                expected_benefit=f"Consistent retrieval of validated lesson (confidence={confidence:.2f}, evidence={count} chains)",
                status="proposed",
                project=project,
                user_id=user_id,
                meta={
                    "lesson_normalized": norm_key,
                    "episode_chain_ids": episode_ids,
                    "evidence_count": count,
                    "confidence": confidence,
                    "auto_promote": False,
                },
            )
            session.add(proposal)
            proposals_created += 1
        else:
            # Below approval threshold but above store threshold:
            # Create procedural memory in active state (low trust, needs more evidence)
            mem_id = f"pr_{uuid.uuid4().hex[:16]}"
            mem = Memory(
                id=mem_id,
                layer="procedural",
                content=canonical,
                project=project,
                importance=_PROCEDURAL_IMPORTANCE,
                memory_state=MemoryState.ACTIVE,
                trust_score=_PROCEDURAL_BASE_TRUST,
                verification_status=TrustLevel.TRUSTED_SYSTEM_OBSERVED,
                confidence=confidence,
                source_type="episodic_promotion",
                evidence_count=count,
                derived_from_episode_ids=episode_ids,
                valid_from=datetime.now(UTC),
            )
            session.add(mem)
            try:
                from storage import vector_store
                vector_store.upsert(
                    "procedural", mem_id, canonical,
                    project_id=project,
                    importance=_PROCEDURAL_IMPORTANCE,
                    trust_score=_PROCEDURAL_BASE_TRUST,
                    verification_status=TrustLevel.TRUSTED_SYSTEM_OBSERVED,
                    memory_state=MemoryState.ACTIVE,
                )
            except Exception as exc:
                logger.warning("procedural_promoter: vector upsert failed: %s", exc)
            memories_created += 1

    if memories_created or proposals_created or evidence_updated:
        await session.commit()

    logger.info(
        "procedural_promoter: lessons=%d candidates=%d memories=%d proposals=%d evidence_updated=%d",
        len(chains),
        len([g for g in groups.values() if len(g) >= _MIN_LESSON_COUNT]),
        memories_created,
        proposals_created,
        evidence_updated,
    )
    return {
        "lessons_scanned": len(chains),
        "candidates_found": len([g for g in groups.values() if len(g) >= _MIN_LESSON_COUNT]),
        "memories_created": memories_created,
        "proposals_created": proposals_created,
        "evidence_updated": evidence_updated,
    }


async def apply_feedback_to_procedural(
    session: AsyncSession,
    memory_id: str,
    outcome: str,
    *,
    trust_delta: float,
) -> bool:
    """Update last_success_at/last_failure_at and evidence_count for procedural memories.

    Called by the feedback endpoint for procedural-layer memories only.
    Returns True if the memory was updated.
    """
    mem = await session.get(Memory, memory_id)
    if not mem or mem.layer != "procedural" or mem.deleted_at:
        return False

    now = datetime.now(UTC)
    if outcome == "success":
        mem.last_success_at = now
        mem.evidence_count = (mem.evidence_count or 0) + 1
    else:
        mem.last_failure_at = now

    session.add(mem)
    return True
