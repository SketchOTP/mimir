"""Automatic retrieval feedback inference engine (P9).

Infers positive or negative retrieval outcomes from retrieval session data
without requiring explicit operator feedback for every event.

Safety constraints (non-negotiable):
  - Inferred reinforcement is small (+0.01 positive, -0.03 negative)
  - Bounded: trust never exceeds 0.99 or drops below 0.01
  - Never infer positive outcomes from incomplete evidence
  - Never reactivate quarantined or archived memories
  - Never auto-promote procedural changes
  - All inference is idempotent (inference_applied flag prevents double-application)

Positive inference triggers:
  - retrieval occurred AND task_outcome == "success"
    AND no rollback AND no correction AND no harmful outcome

Negative inference triggers:
  - retrieval occurred AND (rollback_id is set OR has_correction OR has_harmful_outcome)
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import Memory, RetrievalSession, RetrievalFeedback, LifecycleEvent
from memory.trust import MemoryState

logger = logging.getLogger(__name__)

# Conservative inference deltas — much smaller than explicit feedback
_INFER_POSITIVE_DELTA = +0.01
_INFER_NEGATIVE_DELTA = -0.03
_TRUST_MAX = 0.99
_TRUST_MIN = 0.01


async def infer_retrieval_outcomes(session: AsyncSession) -> dict:
    """Scan retrieval sessions with known outcomes and apply inferred feedback.

    Returns: {positive_inferred, negative_inferred, sessions_processed}
    """
    result = await session.execute(
        select(RetrievalSession).where(
            RetrievalSession.inference_applied.is_(False),
            RetrievalSession.task_outcome.isnot(None),
        )
    )
    pending_sessions = result.scalars().all()

    positive_inferred = 0
    negative_inferred = 0

    for rs in pending_sessions:
        memory_ids = rs.retrieved_memory_ids or []
        if not memory_ids:
            rs.inference_applied = True
            session.add(rs)
            continue

        is_negative = (
            rs.rollback_id is not None
            or rs.has_correction
            or rs.has_harmful_outcome
            or rs.task_outcome == "failure"
        )
        is_positive = (
            rs.task_outcome == "success"
            and not rs.rollback_id
            and not rs.has_correction
            and not rs.has_harmful_outcome
        )

        if is_positive:
            n = await _apply_inferred_delta(
                session, memory_ids, _INFER_POSITIVE_DELTA, "inferred_success"
            )
            positive_inferred += n
        elif is_negative:
            n = await _apply_inferred_delta(
                session, memory_ids, _INFER_NEGATIVE_DELTA, "inferred_failure"
            )
            negative_inferred += n

        rs.inference_applied = True
        session.add(rs)

    if pending_sessions:
        await session.commit()

    logger.info(
        "feedback_inference: sessions=%d positive=%d negative=%d",
        len(pending_sessions),
        positive_inferred,
        negative_inferred,
    )
    return {
        "sessions_processed": len(pending_sessions),
        "positive_inferred": positive_inferred,
        "negative_inferred": negative_inferred,
    }


async def _apply_inferred_delta(
    session: AsyncSession,
    memory_ids: list[str],
    delta: float,
    reason: str,
) -> int:
    """Apply trust delta to a list of memories. Returns count actually updated."""
    if not memory_ids:
        return 0

    result = await session.execute(
        select(Memory).where(
            Memory.id.in_(memory_ids),
            Memory.deleted_at.is_(None),
        )
    )
    memories = result.scalars().all()
    updated = 0

    for mem in memories:
        # Safety: never touch blocked states
        if mem.memory_state in MemoryState.BLOCKED:
            continue

        old_trust = mem.trust_score or 0.7
        new_trust = max(_TRUST_MIN, min(_TRUST_MAX, old_trust + delta))

        if abs(new_trust - old_trust) < 0.0005:
            continue

        mem.trust_score = round(new_trust, 4)
        session.add(mem)

        # Persist as a RetrievalFeedback with inferred outcome tag
        outcome = "success" if delta > 0 else "failure"
        session.add(RetrievalFeedback(
            id=uuid.uuid4().hex,
            memory_id=mem.id,
            outcome=outcome,
            reason=f"auto_inferred:{reason}",
            user_id=None,
        ))

        # Lifecycle audit
        event_type = "trust_increased" if delta > 0 else "trust_decreased"
        session.add(LifecycleEvent(
            id=uuid.uuid4().hex,
            memory_id=mem.id,
            event_type=event_type,
            from_state=mem.memory_state,
            to_state=mem.memory_state,
            trust_before=old_trust,
            trust_after=new_trust,
            reason=f"feedback_inference:{reason}",
        ))

        updated += 1

    return updated
