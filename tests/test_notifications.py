"""Notification stub and delivery tests.

Verifies:
  - When PWA/Slack are unconfigured, notifications are recorded as 'stubbed'
  - Notification records are visible via GET /api/notifications
  - Approval creation succeeds even when notification providers raise exceptions
"""

import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_notification_stubbed_when_unconfigured(client):
    """Unconfigured providers produce 'stubbed' Notification rows, not silence."""
    # Create improvement and request approval (no VAPID/Slack configured in tests)
    r = await client.post("/api/improvements/propose", json={
        "improvement_type": "memory_policy",
        "title": "Notification stub test",
        "reason": "verifying stub behavior",
        "current_behavior": "default memory policy",
        "proposed_behavior": "tuned memory policy",
        "expected_benefit": "better dedup",
    })
    assert r.status_code == 200
    imp_id = r.json()["id"]

    r = await client.post(f"/api/approvals?improvement_id={imp_id}", json={})
    assert r.status_code == 200
    result = r.json()
    approval_id = result["approval"]["id"]

    # Notification flags must be False (no real delivery, unconfigured)
    assert result["notifications"]["slack"] is False
    assert result["notifications"]["pwa"] is False

    # But stubbed records must be visible for this specific approval
    r = await client.get("/api/notifications")
    assert r.status_code == 200
    notifs = r.json()["notifications"]
    stubbed = [n for n in notifs if n["status"] == "stubbed" and n["approval_id"] == approval_id]
    assert len(stubbed) >= 2, (
        f"Expected ≥2 stubbed notifications (pwa + slack) for approval {approval_id}, "
        f"got {len(stubbed)}: {stubbed}"
    )
    channels = {n["channel"] for n in stubbed}
    assert "slack" in channels
    assert "pwa" in channels


@pytest.mark.asyncio
async def test_approval_flow_survives_notification_exception(client):
    """Approval creation completes even when notification providers raise."""
    r = await client.post("/api/improvements/propose", json={
        "improvement_type": "retrieval_tune",
        "title": "Notification fault-tolerance test",
        "reason": "testing resilience",
        "current_behavior": "default retrieval",
        "proposed_behavior": "tuned retrieval",
        "expected_benefit": "better relevance",
    })
    imp_id = r.json()["id"]

    with patch(
        "notifications.slack_notifier.send_approval_request",
        new=AsyncMock(side_effect=Exception("Slack is down")),
    ):
        with patch(
            "notifications.pwa_push.broadcast",
            new=AsyncMock(side_effect=Exception("Push service unavailable")),
        ):
            r = await client.post(f"/api/approvals?improvement_id={imp_id}", json={})
            assert r.status_code == 200, f"Approval must succeed despite notification errors: {r.text}"
            data = r.json()
            assert "approval" in data
            assert data["approval"]["id"]


@pytest.mark.asyncio
async def test_notification_status_visible_via_api(client):
    """GET /api/notifications returns notification records with correct shape."""
    r = await client.get("/api/notifications")
    assert r.status_code == 200
    data = r.json()
    assert "notifications" in data
    for n in data["notifications"]:
        assert "id" in n
        assert "channel" in n
        assert "status" in n
        assert n["status"] in ("sent", "stubbed", "pending", "failed", "read")


@pytest.mark.asyncio
async def test_notification_filter_by_status(client):
    """GET /api/notifications?status=stubbed returns only stubbed records."""
    r = await client.get("/api/notifications", params={"status": "stubbed"})
    assert r.status_code == 200
    notifs = r.json()["notifications"]
    assert all(n["status"] == "stubbed" for n in notifs)


@pytest.mark.asyncio
async def test_get_notification_by_id(client):
    """GET /api/notifications/:id returns a single notification with all required fields."""
    # First create a notification record via approval flow
    r = await client.post("/api/improvements/propose", json={
        "improvement_type": "memory_policy",
        "title": "Notification by ID test",
        "reason": "testing",
        "current_behavior": "x",
        "proposed_behavior": "y",
        "expected_benefit": "z",
    })
    imp_id = r.json()["id"]
    r = await client.post(f"/api/approvals?improvement_id={imp_id}", json={})
    assert r.status_code == 200

    # Get the list and pick the first notification ID
    r = await client.get("/api/notifications")
    notifs = r.json()["notifications"]
    assert notifs, "Expected at least one notification record"
    notif_id = notifs[0]["id"]

    # Fetch by ID
    r = await client.get(f"/api/notifications/{notif_id}")
    assert r.status_code == 200
    n = r.json()
    assert n["id"] == notif_id
    assert "channel" in n
    assert "status" in n
    assert "approval_id" in n
    assert "error" in n
    assert "sent_at" in n
    assert "created_at" in n


@pytest.mark.asyncio
async def test_get_notification_by_id_not_found(client):
    """GET /api/notifications/nonexistent returns 404."""
    r = await client.get("/api/notifications/nonexistent-id-00000")
    assert r.status_code == 404
