"""Structured JSON logging for Mimir.

Usage:
    from mimir.logging import configure_logging, log_event

    configure_logging()          # call once at startup

    log_event("memory_store", user_id="u1", project_id="p1",
              component="episodic_store", duration_ms=12, status="ok")
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, UTC
from typing import Any


class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Merge any structured fields attached via extra={}
        for key, val in record.__dict__.items():
            if key not in _STANDARD_LOG_KEYS and not key.startswith("_"):
                payload[key] = val
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_STANDARD_LOG_KEYS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
    | {"message", "asctime"}
)

_configured = False


def configure_logging(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JsonFormatter())
        root.addHandler(handler)
    else:
        for handler in root.handlers:
            handler.setFormatter(_JsonFormatter())
    _configured = True


_event_logger = logging.getLogger("mimir.events")


def log_event(
    event_type: str,
    *,
    user_id: str | None = None,
    project_id: str | None = None,
    component: str | None = None,
    duration_ms: int | None = None,
    status: str = "ok",
    error: str | None = None,
    **extra: Any,
) -> None:
    """Emit a structured event log entry at INFO (or ERROR on failure)."""
    fields: dict[str, Any] = {
        "event_type": event_type,
        "status": status,
    }
    if user_id is not None:
        fields["user_id"] = user_id
    if project_id is not None:
        fields["project_id"] = project_id
    if component is not None:
        fields["component"] = component
    if duration_ms is not None:
        fields["duration_ms"] = duration_ms
    if error is not None:
        fields["error"] = error
    fields.update(extra)

    level = logging.ERROR if status == "error" else logging.INFO
    _event_logger.log(level, event_type, extra=fields)
