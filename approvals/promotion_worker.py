"""Promote approved improvements into the live system."""

from __future__ import annotations

import logging
from datetime import datetime, UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import ApprovalRequest, ImprovementProposal
from skills import skill_registry

logger = logging.getLogger(__name__)


async def promote_approved(session: AsyncSession) -> list[str]:
    """Find all approved-but-unprocessed proposals and promote them."""
    q = select(ApprovalRequest).where(ApprovalRequest.status == "approved")
    result = await session.execute(q)
    approvals = list(result.scalars())

    promoted = []
    for approval in approvals:
        if not approval.improvement_id:
            continue
        imp = await session.get(ImprovementProposal, approval.improvement_id)
        if not imp or imp.status != "approved":
            continue

        try:
            await _apply_improvement(session, imp)
            imp.status = "promoted"
            await session.commit()
            promoted.append(imp.id)
            logger.info("Promoted improvement %s (%s)", imp.id, imp.improvement_type)
        except Exception as e:
            await session.rollback()
            logger.error("Failed to promote improvement %s: %s", imp.id, e)

    return promoted


async def _apply_improvement(session: AsyncSession, imp: ImprovementProposal) -> None:
    """Route improvement to the correct subsystem."""
    itype = imp.improvement_type

    if itype == "skill_update" and imp.meta:
        skill_id = imp.meta.get("skill_id")
        updates = imp.meta.get("updates", {})
        if skill_id and updates:
            await skill_registry.update(session, skill_id, updates)

    elif itype == "skill_refine" and imp.meta:
        skill_id = imp.meta.get("skill_id")
        if skill_id:
            await skill_registry.set_status(session, skill_id, "active")

    elif itype in ("retrieval_tune", "context_tune", "memory_policy"):
        # Store as a semantic procedural rule that can be read by the relevant engine
        from memory import semantic_store
        await semantic_store.store(
            session,
            content=f"[system_policy:{itype}] {imp.proposed_behavior}",
            importance=0.9,
            project=imp.project,
            meta={"improvement_id": imp.id, "type": "system_policy"},
        )

    # For other types: record in meta that it was promoted
    if imp.meta is None:
        imp.meta = {}
    imp.meta["promoted_at"] = datetime.now(UTC).isoformat()


async def backfill_promoted_at(session: AsyncSession) -> int:
    """
    Set promoted_at in meta for promoted improvements that are missing it.
    The rollback watcher skips improvements without this timestamp, so old
    promotions created before this field was written need to be backfilled.
    """
    from storage.models import ApprovalRequest

    q = select(ImprovementProposal).where(ImprovementProposal.status == "promoted")
    result = await session.execute(q)
    count = 0
    for imp in result.scalars():
        if (imp.meta or {}).get("promoted_at"):
            continue
        # Prefer the approval's decided_at, then the improvement's updated_at, then now
        apr_q = await session.execute(
            select(ApprovalRequest).where(ApprovalRequest.improvement_id == imp.id)
        )
        approval = apr_q.scalars().first()
        if approval and approval.decided_at:
            promoted_at = approval.decided_at.isoformat()
        elif imp.updated_at:
            promoted_at = imp.updated_at.isoformat()
        else:
            promoted_at = datetime.now(UTC).isoformat()
        imp.meta = {**(imp.meta or {}), "promoted_at": promoted_at}
        count += 1
    if count:
        await session.commit()
    logger.info("Backfilled promoted_at for %d improvements", count)
    return count
