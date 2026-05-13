"""Slack interaction security tests.

Covers:
  1. Valid Slack signature accepted
  2. Invalid Slack signature rejected (403)
  3. Old timestamp rejected (403)
  4. Unknown approval ID handled cleanly (not a 500)
  5. Duplicate approval decision is a no-op (not a 500)
  6. Unauthenticated approval API rejected when auth is enabled
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
from unittest.mock import patch

import pytest


# ─── Helpers ─────────────────────────────────────────────────────────────────

_SECRET = "test-signing-secret"


def _make_payload(action_id: str, value: str, user: str = "U001") -> str:
    payload = {
        "type": "block_actions",
        "user": {"id": user, "username": user},
        "actions": [{"action_id": action_id, "value": value}],
    }
    return "payload=" + urllib.parse.quote(json.dumps(payload))


def _sign(body: str, timestamp: str, secret: str = _SECRET) -> str:
    basestring = f"v0:{timestamp}:{body}"
    return "v0=" + hmac.new(
        secret.encode("utf-8"),
        basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# ─── Tests ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_valid_slack_signature_accepted(client):
    """A correctly signed request to an existing approval is processed (not 403)."""
    r = await client.post("/api/improvements/propose", json={
        "improvement_type": "retrieval_tune",
        "title": "Slack sig test improvement",
        "reason": "testing",
        "current_behavior": "default",
        "proposed_behavior": "tuned",
        "expected_benefit": "better",
    })
    imp_id = r.json()["id"]
    r = await client.post(f"/api/approvals?improvement_id={imp_id}", json={})
    approval_id = r.json()["approval"]["id"]

    body = _make_payload("mimir_approve", f"approve:{approval_id}")
    ts = str(int(time.time()))
    sig = _sign(body, ts)

    with patch("api.routes.slack.get_settings") as mock_gs:
        mock_gs.return_value.slack_signing_secret = _SECRET
        r = await client.post(
            "/api/slack/interactions",
            content=body,
            headers={
                "x-slack-request-timestamp": ts,
                "x-slack-signature": sig,
                "content-type": "application/x-www-form-urlencoded",
            },
        )
    assert r.status_code != 403, f"Valid signature was rejected: {r.text}"


@pytest.mark.asyncio
async def test_invalid_slack_signature_rejected(client):
    """A tampered/wrong signature returns 403."""
    body = _make_payload("mimir_approve", "approve:doesntmatter")
    ts = str(int(time.time()))
    bad_sig = "v0=badbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbad"

    with patch("api.routes.slack.get_settings") as mock_gs:
        mock_gs.return_value.slack_signing_secret = _SECRET
        r = await client.post(
            "/api/slack/interactions",
            content=body,
            headers={
                "x-slack-request-timestamp": ts,
                "x-slack-signature": bad_sig,
                "content-type": "application/x-www-form-urlencoded",
            },
        )
    assert r.status_code == 403, f"Expected 403 for bad signature, got {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_old_timestamp_rejected(client):
    """A request with a timestamp older than 5 minutes returns 403."""
    body = _make_payload("mimir_approve", "approve:doesntmatter")
    old_ts = str(int(time.time()) - 400)  # 400s ago — outside 5-min window
    sig = _sign(body, old_ts)

    with patch("api.routes.slack.get_settings") as mock_gs:
        mock_gs.return_value.slack_signing_secret = _SECRET
        r = await client.post(
            "/api/slack/interactions",
            content=body,
            headers={
                "x-slack-request-timestamp": old_ts,
                "x-slack-signature": sig,
                "content-type": "application/x-www-form-urlencoded",
            },
        )
    assert r.status_code == 403, f"Expected 403 for old timestamp, got {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_unknown_approval_id_handled_cleanly(client):
    """Approving an unknown ID returns a graceful response, not a 500."""
    body = _make_payload("mimir_approve", "approve:nonexistent-id-xyz")
    ts = str(int(time.time()))

    # No signing secret configured — passes through
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
    data = r.json()
    assert "text" in data
    assert "not found" in data["text"].lower() or "already" in data["text"].lower()


@pytest.mark.asyncio
async def test_duplicate_approval_decision_is_noop(client):
    """Approving an already-approved request returns a safe message, not 500."""
    r = await client.post("/api/improvements/propose", json={
        "improvement_type": "memory_policy",
        "title": "Duplicate decision test",
        "reason": "testing duplicate guard",
        "current_behavior": "x",
        "proposed_behavior": "y",
        "expected_benefit": "z",
    })
    imp_id = r.json()["id"]
    r = await client.post(f"/api/approvals?improvement_id={imp_id}", json={})
    approval_id = r.json()["approval"]["id"]

    # First approve via standard API
    r = await client.post(f"/api/approvals/{approval_id}/approve", json={})
    assert r.status_code == 200

    # Second attempt via Slack — must not 500
    body = _make_payload("mimir_approve", f"approve:{approval_id}")
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
    data = r.json()
    assert "text" in data


@pytest.mark.asyncio
async def test_unauthenticated_approval_api_rejected_in_production():
    """When MIMIR_ENV != development, the /approvals endpoints require a valid API key."""
    import os
    from httpx import AsyncClient, ASGITransport
    from storage.database import init_db

    # Stand up a second app instance with env=production
    old_env = os.environ.get("MIMIR_ENV", "development")
    old_key = os.environ.get("MIMIR_API_KEY", "local-dev-key")
    os.environ["MIMIR_ENV"] = "production"
    os.environ["MIMIR_API_KEY"] = "secret-prod-key"

    try:
        # Clear lru_cache so new Settings are picked up
        from mimir.config import get_settings
        get_settings.cache_clear()

        from api.main import app as _app
        await init_db()

        async with AsyncClient(transport=ASGITransport(app=_app), base_url="http://test") as c:
            # No API key → should get 401
            r = await c.get("/api/approvals")
            assert r.status_code == 401, (
                f"Expected 401 without API key in production mode, got {r.status_code}"
            )

            # Wrong API key → 401
            r = await c.get("/api/approvals", headers={"x-api-key": "wrong"})
            assert r.status_code == 401

            # Correct API key → 200
            r = await c.get("/api/approvals", headers={"x-api-key": "secret-prod-key"})
            assert r.status_code == 200
    finally:
        os.environ["MIMIR_ENV"] = old_env
        os.environ["MIMIR_API_KEY"] = old_key
        get_settings.cache_clear()
