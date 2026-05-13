"""Slack notification delivery for approval requests."""

from __future__ import annotations

import logging

from mimir.config import get_settings

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(get_settings().slack_bot_token)


async def send_approval_request(approval_summary: dict) -> bool:
    """Post an approval card to the configured Slack channel."""
    if not is_configured():
        logger.debug("Slack not configured — skipping")
        return False

    try:
        from slack_sdk.web.async_client import AsyncWebClient

        settings = get_settings()
        client = AsyncWebClient(token=settings.slack_bot_token)

        blocks = _build_approval_blocks(approval_summary)
        await client.chat_postMessage(
            channel=settings.slack_approval_channel,
            text=f"Mimir: Approval needed — {approval_summary.get('title', 'unknown')}",
            blocks=blocks,
        )
        return True
    except ImportError:
        logger.warning("slack_sdk not installed — Slack notifications unavailable")
        return False
    except Exception as e:
        logger.error("Slack notification failed: %s", e)
        return False


async def send_message(text: str, channel: str | None = None) -> bool:
    if not is_configured():
        return False
    try:
        from slack_sdk.web.async_client import AsyncWebClient

        settings = get_settings()
        client = AsyncWebClient(token=settings.slack_bot_token)
        await client.chat_postMessage(
            channel=channel or settings.slack_approval_channel,
            text=text,
        )
        return True
    except Exception as e:
        logger.error("Slack message failed: %s", e)
        return False


def _build_approval_blocks(summary: dict) -> list[dict]:
    risk_emoji = {"low": ":white_check_mark:", "medium": ":warning:", "high": ":rotating_light:"}
    risk = summary.get("risk", "low")

    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Mimir Approval: {summary.get('title', '')}"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Type:* {summary.get('type', '')}"},
                {"type": "mrkdwn", "text": f"*Risk:* {risk_emoji.get(risk, '')} {risk}"},
                {"type": "mrkdwn", "text": f"*Current:* {summary.get('current_behavior', '')}"},
                {"type": "mrkdwn", "text": f"*Proposed:* {summary.get('proposed_behavior', '')}"},
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Reason:* {summary.get('reason', '')}\n"
                    f"*Expected Benefit:* {summary.get('expected_benefit', '')}\n"
                    f"*Test Result:* {summary.get('test_result', 'pending')}"
                ),
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "value": f"approve:{summary.get('id')}",
                    "action_id": "mimir_approve",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "style": "danger",
                    "value": f"reject:{summary.get('id')}",
                    "action_id": "mimir_reject",
                },
            ],
        },
    ]
