"""Dashboard and push subscription endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import PushSubscriptionIn
from api.deps import UserContext, get_current_user
from storage.database import get_session
from storage.models import PushSubscription, Rollback, Reflection, Notification
from metrics.metrics_engine import get_dashboard_metrics
from notifications.pwa_push import get_public_key

router = APIRouter(tags=["dashboard"])


@router.get("/health")
async def health_api():
    """Backward-compat alias for /health — no auth required."""
    return {"status": "ok", "service": "mimir"}


@router.get("/dashboard")
async def dashboard(
    project: str | None = None,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    metrics = await get_dashboard_metrics(session)

    rollback_q = await session.execute(
        select(Rollback).order_by(Rollback.created_at.desc()).limit(5)
    )
    rollbacks = [
        {"id": r.id, "target_id": r.target_id, "reason": r.reason, "created_at": r.created_at.isoformat()}
        for r in rollback_q.scalars()
    ]

    ref_q_stmt = select(Reflection).order_by(Reflection.created_at.desc()).limit(3)
    if not current_user.is_dev:
        ref_q_stmt = ref_q_stmt.where(Reflection.user_id == current_user.id)
    ref_q = await session.execute(ref_q_stmt)
    lessons = []
    for ref in ref_q.scalars():
        lessons.extend(ref.lessons[:2])

    return {
        **metrics,
        "recent_rollbacks": rollbacks,
        "recent_lessons": lessons[:5],
    }


@router.post("/push/subscribe")
async def subscribe_push(
    body: PushSubscriptionIn,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    sub = PushSubscription(
        id=uuid.uuid4().hex,
        endpoint=body.endpoint,
        keys=body.keys,
        user_agent=body.user_agent,
    )
    session.add(sub)
    await session.commit()
    return {"ok": True, "id": sub.id}


@router.get("/push/vapid-key")
async def get_vapid_key():
    key = get_public_key()
    return {"public_key": key}


def _notification_dict(n: Notification) -> dict:
    return {
        "id": n.id,
        "channel": n.channel,
        "title": n.title,
        "body": n.body,
        "status": n.status,
        "approval_id": n.approval_id,
        "error": n.error,
        "sent_at": n.sent_at.isoformat() if n.sent_at else None,
        "created_at": n.created_at.isoformat(),
    }


@router.get("/notifications")
async def list_notifications(
    status: str | None = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    q = select(Notification).order_by(Notification.created_at.desc()).limit(limit)
    if not current_user.is_dev:
        q = q.where(Notification.user_id == current_user.id)
    if status:
        q = q.where(Notification.status == status)
    result = await session.execute(q)
    notifs = result.scalars().all()
    return {"notifications": [_notification_dict(n) for n in notifs]}


@router.get("/notifications/{notification_id}")
async def get_notification(
    notification_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    n = await session.get(Notification, notification_id)
    if not n:
        raise HTTPException(404, "Notification not found")
    if not current_user.is_dev and n.user_id and n.user_id != current_user.id:
        raise HTTPException(404, "Notification not found")
    return _notification_dict(n)
