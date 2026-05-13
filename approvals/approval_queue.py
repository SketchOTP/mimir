"""Manage the approval queue: list, approve, reject."""

from __future__ import annotations

import uuid
from datetime import datetime, UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import ApprovalAuditLog, ApprovalRequest, ImprovementProposal


async def get(session: AsyncSession, approval_id: str) -> ApprovalRequest | None:
    return await session.get(ApprovalRequest, approval_id)


async def list_pending(session: AsyncSession, limit: int = 50) -> list[ApprovalRequest]:
    q = (
        select(ApprovalRequest)
        .where(ApprovalRequest.status == "pending")
        .order_by(ApprovalRequest.created_at.asc())
        .limit(limit)
    )
    result = await session.execute(q)
    return list(result.scalars())


async def list_all(
    session: AsyncSession, status: str | None = None, limit: int = 100
) -> list[ApprovalRequest]:
    q = select(ApprovalRequest).order_by(ApprovalRequest.created_at.desc()).limit(limit)
    if status:
        q = q.where(ApprovalRequest.status == status)
    result = await session.execute(q)
    return list(result.scalars())


async def approve(
    session: AsyncSession,
    approval_id: str,
    reviewer_note: str | None = None,
    source: str = "api",
    actor: str | None = None,
    actor_user_id: str | None = None,
    actor_display_name: str | None = None,
) -> ApprovalRequest | None:
    approval = await get(session, approval_id)
    if not approval or approval.status != "pending":
        return None
    prev_status = approval.status
    approval.status = "approved"
    approval.decided_at = datetime.now(UTC)
    approval.reviewer_note = reviewer_note
    if approval.improvement_id:
        imp = await session.get(ImprovementProposal, approval.improvement_id)
        if imp:
            imp.status = "approved"
    session.add(ApprovalAuditLog(
        id=uuid.uuid4().hex,
        approval_id=approval_id,
        decision="approved",
        actor=actor,
        actor_user_id=actor_user_id,
        actor_display_name=actor_display_name,
        source=source,
        previous_status=prev_status,
        new_status="approved",
        reason=reviewer_note,
    ))
    await session.commit()
    return approval


async def reject(
    session: AsyncSession,
    approval_id: str,
    reviewer_note: str | None = None,
    source: str = "api",
    actor: str | None = None,
    actor_user_id: str | None = None,
    actor_display_name: str | None = None,
) -> ApprovalRequest | None:
    approval = await get(session, approval_id)
    if not approval or approval.status != "pending":
        return None
    prev_status = approval.status
    approval.status = "rejected"
    approval.decided_at = datetime.now(UTC)
    approval.reviewer_note = reviewer_note
    if approval.improvement_id:
        imp = await session.get(ImprovementProposal, approval.improvement_id)
        if imp:
            imp.status = "rejected"
    session.add(ApprovalAuditLog(
        id=uuid.uuid4().hex,
        approval_id=approval_id,
        decision="rejected",
        actor=actor,
        actor_user_id=actor_user_id,
        actor_display_name=actor_display_name,
        source=source,
        previous_status=prev_status,
        new_status="rejected",
        reason=reviewer_note,
    ))
    await session.commit()
    return approval


async def expire_stale(session: AsyncSession) -> int:
    """Mark expired approvals as expired."""
    q = select(ApprovalRequest).where(
        ApprovalRequest.status == "pending",
        ApprovalRequest.expires_at < datetime.now(UTC),
    )
    result = await session.execute(q)
    count = 0
    for approval in result.scalars():
        approval.status = "expired"
        session.add(ApprovalAuditLog(
            id=uuid.uuid4().hex,
            approval_id=approval.id,
            decision="expired",
            actor=None,
            actor_user_id=None,
            actor_display_name=None,
            source="system",
            previous_status="pending",
            new_status="expired",
            reason="TTL exceeded",
        ))
        count += 1
    if count:
        await session.commit()
    return count
