"""Approval workflow endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import ApprovalDecisionIn, ApprovalOut
from api.deps import UserContext, get_current_user
from storage.database import get_session
from approvals import approval_queue
from notifications import pwa_push, slack_notifier

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.post("")
async def create_approval(
    improvement_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    from storage.models import ImprovementProposal
    from reflections.improvement_planner import create_approval_request

    imp = await session.get(ImprovementProposal, improvement_id)
    if not imp:
        raise HTTPException(404, "Improvement not found")
    if not current_user.is_dev and imp.user_id and imp.user_id != current_user.id:
        raise HTTPException(404, "Improvement not found")

    approval = await create_approval_request(session, imp)
    approval.user_id = current_user.id if not current_user.is_dev else None

    # Send notifications — errors must not block approval creation
    sent_slack = False
    sent_pwa = 0
    try:
        sent_slack = await slack_notifier.send_approval_request(approval.summary)
    except Exception:
        pass
    push_data = {
        "approval_id": approval.id,
        "title": approval.title,
        "risk": imp.risk if imp else "low",
        "url": f"/approvals/{approval.id}",
    }
    try:
        sent_pwa = await pwa_push.broadcast(session, approval.title, str(imp.reason), push_data)
    except Exception:
        pass

    from storage.models import Notification
    import uuid
    uid = current_user.id if not current_user.is_dev else None
    if sent_slack:
        session.add(Notification(
            id=uuid.uuid4().hex, channel="slack", title=approval.title,
            body=str(imp.reason), approval_id=approval.id, status="sent", user_id=uid,
        ))
    elif not slack_notifier.is_configured():
        session.add(Notification(
            id=uuid.uuid4().hex, channel="slack", title=approval.title,
            body=str(imp.reason), approval_id=approval.id, status="stubbed", user_id=uid,
        ))
    if sent_pwa:
        session.add(Notification(
            id=uuid.uuid4().hex, channel="pwa", title=approval.title,
            body=str(imp.reason), approval_id=approval.id, status="sent", user_id=uid,
        ))
    elif not pwa_push.is_configured():
        session.add(Notification(
            id=uuid.uuid4().hex, channel="pwa", title=approval.title,
            body=str(imp.reason), approval_id=approval.id, status="stubbed", user_id=uid,
        ))
    if sent_slack or sent_pwa > 0:
        approval.notification_sent = True
    await session.commit()

    return {"approval": ApprovalOut.model_validate(approval), "notifications": {"slack": sent_slack, "pwa": sent_pwa > 0}}


@router.get("/{approval_id}", response_model=ApprovalOut)
async def get_approval(
    approval_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    from storage.models import ApprovalRequest
    approval = await session.get(ApprovalRequest, approval_id)
    if not approval:
        raise HTTPException(404, "Approval not found")
    if not current_user.is_dev and approval.user_id and approval.user_id != current_user.id:
        raise HTTPException(404, "Approval not found")
    return approval


@router.get("")
async def list_approvals(
    status: str | None = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    from storage.models import ApprovalRequest
    q = select(ApprovalRequest).order_by(ApprovalRequest.created_at.desc()).limit(limit)
    if not current_user.is_dev:
        q = q.where(ApprovalRequest.user_id == current_user.id)
    if status:
        q = q.where(ApprovalRequest.status == status)
    result = await session.execute(q)
    approvals = result.scalars().all()
    return {"approvals": [ApprovalOut.model_validate(a) for a in approvals]}


@router.post("/{approval_id}/approve", response_model=ApprovalOut)
async def approve(
    approval_id: str,
    body: ApprovalDecisionIn,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    from storage.models import ApprovalRequest
    approval = await session.get(ApprovalRequest, approval_id)
    if not approval:
        raise HTTPException(404, "Approval not found or not pending")
    if not current_user.is_dev and approval.user_id and approval.user_id != current_user.id:
        raise HTTPException(404, "Approval not found or not pending")

    result = await approval_queue.approve(
        session, approval_id,
        reviewer_note=body.reviewer_note,
        source="dashboard",
        actor=current_user.display_name,
        actor_user_id=current_user.id if not current_user.is_dev else None,
        actor_display_name=current_user.display_name if not current_user.is_dev else None,
    )
    if not result:
        raise HTTPException(404, "Approval not found or not pending")
    return result


@router.post("/{approval_id}/reject", response_model=ApprovalOut)
async def reject(
    approval_id: str,
    body: ApprovalDecisionIn,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    from storage.models import ApprovalRequest
    approval = await session.get(ApprovalRequest, approval_id)
    if not approval:
        raise HTTPException(404, "Approval not found or not pending")
    if not current_user.is_dev and approval.user_id and approval.user_id != current_user.id:
        raise HTTPException(404, "Approval not found or not pending")

    result = await approval_queue.reject(
        session, approval_id,
        reviewer_note=body.reviewer_note,
        source="dashboard",
        actor=current_user.display_name,
        actor_user_id=current_user.id if not current_user.is_dev else None,
        actor_display_name=current_user.display_name if not current_user.is_dev else None,
    )
    if not result:
        raise HTTPException(404, "Approval not found or not pending")
    return result
