"""Approval audit trail tests.

Every approval decision must produce an ApprovalAuditLog row recording:
  approval_id, decision, actor, source, timestamp, reason,
  previous_status, new_status.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from storage.models import ApprovalAuditLog


async def _create_approval(client) -> str:
    r = await client.post("/api/improvements/propose", json={
        "improvement_type": "retrieval_tune",
        "title": "Audit trail test",
        "reason": "testing audit",
        "current_behavior": "default",
        "proposed_behavior": "tuned",
        "expected_benefit": "better recall",
    })
    imp_id = r.json()["id"]
    r = await client.post(f"/api/approvals?improvement_id={imp_id}", json={})
    return r.json()["approval"]["id"]


@pytest.mark.asyncio
async def test_approve_writes_audit_record(client):
    """Approving via dashboard API writes an audit log row."""
    from storage.database import get_session

    approval_id = await _create_approval(client)
    r = await client.post(f"/api/approvals/{approval_id}/approve", json={"reviewer_note": "LGTM"})
    assert r.status_code == 200

    async for session in get_session():
        result = await session.execute(
            select(ApprovalAuditLog).where(ApprovalAuditLog.approval_id == approval_id)
        )
        rows = result.scalars().all()
        break

    assert len(rows) == 1
    row = rows[0]
    assert row.decision == "approved"
    assert row.source == "dashboard"
    assert row.previous_status == "pending"
    assert row.new_status == "approved"
    assert row.reason == "LGTM"


@pytest.mark.asyncio
async def test_reject_writes_audit_record(client):
    """Rejecting via dashboard API writes an audit log row."""
    from storage.database import get_session

    approval_id = await _create_approval(client)
    r = await client.post(f"/api/approvals/{approval_id}/reject", json={"reviewer_note": "Too risky"})
    assert r.status_code == 200

    async for session in get_session():
        result = await session.execute(
            select(ApprovalAuditLog).where(ApprovalAuditLog.approval_id == approval_id)
        )
        rows = result.scalars().all()
        break

    assert len(rows) == 1
    row = rows[0]
    assert row.decision == "rejected"
    assert row.source == "dashboard"
    assert row.previous_status == "pending"
    assert row.new_status == "rejected"
    assert row.reason == "Too risky"


@pytest.mark.asyncio
async def test_slack_approval_writes_audit_record_with_slack_source(client):
    """Approving via the Slack webhook sets source=slack in the audit log."""
    import urllib.parse, json as _json, hashlib, hmac as _hmac, time
    from storage.database import get_session

    approval_id = await _create_approval(client)

    payload_obj = {
        "type": "block_actions",
        "user": {"id": "U999", "username": "slack_tester"},
        "actions": [{"action_id": "mimir_approve", "value": f"approve:{approval_id}"}],
    }
    body = "payload=" + urllib.parse.quote(_json.dumps(payload_obj))
    ts = str(int(time.time()))

    r = await client.post(
        "/api/slack/interactions",
        content=body,
        headers={
            "x-slack-request-timestamp": ts,
            "x-slack-signature": "v0=ignored",
            "content-type": "application/x-www-form-urlencoded",
        },
    )
    assert r.status_code == 200

    async for session in get_session():
        result = await session.execute(
            select(ApprovalAuditLog).where(ApprovalAuditLog.approval_id == approval_id)
        )
        rows = result.scalars().all()
        break

    assert len(rows) == 1
    row = rows[0]
    assert row.source == "slack"
    assert row.actor == "slack_tester"
    assert row.decision == "approved"


@pytest.mark.asyncio
async def test_audit_record_fields_complete(client):
    """Audit row contains all required fields from the spec."""
    from storage.database import get_session

    approval_id = await _create_approval(client)
    await client.post(f"/api/approvals/{approval_id}/approve", json={"reviewer_note": "ok"})

    async for session in get_session():
        result = await session.execute(
            select(ApprovalAuditLog).where(ApprovalAuditLog.approval_id == approval_id)
        )
        row = result.scalars().first()
        break

    assert row is not None
    assert row.approval_id
    assert row.decision in ("approved", "rejected", "expired")
    assert row.source
    assert row.previous_status
    assert row.new_status
    assert row.created_at is not None
