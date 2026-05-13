"""PWA Web Push notification delivery."""

from __future__ import annotations

import json
import logging
from typing import Any

from mimir.config import get_settings

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    s = get_settings()
    return bool(s.vapid_private_key and s.vapid_public_key)


def get_public_key() -> str | None:
    return get_settings().vapid_public_key or None


async def send(
    subscription: dict[str, Any],
    title: str,
    body: str,
    data: dict | None = None,
) -> bool:
    """Send a Web Push notification to a subscribed endpoint."""
    if not is_configured():
        logger.debug("PWA push not configured — skipping")
        return False

    try:
        from pywebpush import webpush, WebPushException

        settings = get_settings()
        payload = json.dumps({"title": title, "body": body, "data": data or {}})

        webpush(
            subscription_info=subscription,
            data=payload,
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={"sub": f"mailto:{settings.vapid_claim_email}"},
        )
        return True
    except ImportError:
        logger.warning("pywebpush not installed — PWA push unavailable")
        return False
    except Exception as e:
        logger.error("PWA push failed: %s", e)
        return False


async def broadcast(
    session,  # AsyncSession
    title: str,
    body: str,
    data: dict | None = None,
) -> int:
    """Send push to all stored subscriptions."""
    from sqlalchemy import select
    from storage.models import PushSubscription

    result = await session.execute(select(PushSubscription))
    subs = result.scalars().all()
    sent = 0
    for sub in subs:
        ok = await send({"endpoint": sub.endpoint, "keys": sub.keys}, title, body, data)
        if ok:
            sent += 1
    return sent
