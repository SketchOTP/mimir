"""In-memory MCP connection tracker. Records last connection event per client."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

_last_connection: dict[str, Any] = {}


def record_mcp_connection(*, user_id: str | None, auth_method: str, client_name: str | None = None) -> None:
    _last_connection.update({
        "connected_at": datetime.now(UTC).isoformat(),
        "user_id": user_id,
        "auth_method": auth_method,
        "client_name": client_name,
    })


def get_mcp_status() -> dict[str, Any] | None:
    return dict(_last_connection) if _last_connection else None
