"""Slack interactive component webhook handler."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from mimir.config import get_settings
from notifications.slack_interactions import (
    extract_action,
    parse_slack_payload,
    verify_slack_signature,
)
from storage.database import get_session
from approvals import approval_queue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/slack", tags=["slack"])


@router.post("/interactions")
async def slack_interactions(
    request: Request,
    x_slack_request_timestamp: str = Header(default=""),
    x_slack_signature: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
):
    settings = get_settings()

    body = await request.body()

    # Verify signing secret if configured; reject if configured but invalid
    if settings.slack_signing_secret:
        if not verify_slack_signature(
            body,
            x_slack_request_timestamp,
            x_slack_signature,
            settings.slack_signing_secret,
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid Slack signature",
            )

    payload = parse_slack_payload(body.decode("utf-8"))
    if not payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing payload")

    action_type, approval_id = extract_action(payload)
    if action_type is None:
        return {"response_type": "ephemeral", "text": "Unknown action — ignoring."}

    if action_type == "view_details":
        return {
            "response_type": "ephemeral",
            "text": f"Open approval details: /approvals/{approval_id}",
        }

    # Determine actor from Slack user info
    actor = _extract_actor(payload)

    if action_type == "approve":
        result = await approval_queue.approve(
            session, approval_id, reviewer_note=f"Approved via Slack by {actor}", source="slack", actor=actor
        )
        if result is None:
            return {"response_type": "ephemeral", "text": "Approval not found or already decided."}
        return {
            "response_type": "in_channel",
            "text": f":white_check_mark: Approved by {actor}",
        }

    if action_type == "reject":
        result = await approval_queue.reject(
            session, approval_id, reviewer_note=f"Rejected via Slack by {actor}", source="slack", actor=actor
        )
        if result is None:
            return {"response_type": "ephemeral", "text": "Approval not found or already decided."}
        return {
            "response_type": "in_channel",
            "text": f":x: Rejected by {actor}",
        }

    return {"response_type": "ephemeral", "text": "Action processed."}


def _extract_actor(payload: dict) -> str:
    user = payload.get("user", {})
    return user.get("username") or user.get("id") or "slack-user"
