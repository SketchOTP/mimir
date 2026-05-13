"""Slack interactive component signature verification and payload parsing."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse


_REPLAY_WINDOW = 300  # 5 minutes


def verify_slack_signature(
    body: bytes,
    timestamp: str,
    signature: str,
    signing_secret: str,
) -> bool:
    """Return True if the Slack signature is valid and the timestamp is fresh."""
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False

    if abs(time.time() - ts) > _REPLAY_WINDOW:
        return False

    basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    computed = "v0=" + hmac.new(
        signing_secret.encode("utf-8"),
        basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(computed, signature)


def parse_slack_payload(raw: str) -> dict:
    """Decode URL-encoded payload=<json> string from Slack interactions."""
    parsed = urllib.parse.parse_qs(raw)
    payload_list = parsed.get("payload", [])
    if not payload_list:
        return {}
    return json.loads(payload_list[0])


def extract_action(payload: dict) -> tuple[str, str] | tuple[None, None]:
    """Return (action_type, approval_id) from a Slack interaction payload.

    action_type is 'approve' | 'reject' | 'view_details'.
    Returns (None, None) when the payload cannot be parsed.
    """
    actions = payload.get("actions", [])
    if not actions:
        return None, None

    action = actions[0]
    action_id = action.get("action_id", "")
    value = action.get("value", "")

    if action_id == "mimir_approve" and value.startswith("approve:"):
        return "approve", value[len("approve:"):]
    if action_id == "mimir_reject" and value.startswith("reject:"):
        return "reject", value[len("reject:"):]
    if action_id == "mimir_view":
        return "view_details", value

    return None, None
