"""Unauthenticated /api/system/doctor endpoint — setup health and fix guidance."""

from __future__ import annotations

import asyncio
import socket
from datetime import UTC, datetime
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mimir.config import get_settings
from mimir.setup_profile import effective_public_url, load_setup_profile, normalize_setup_profile
from mimir.__version__ import __version__
from storage.database import get_session
from storage.models import Memory, User
from api.routes._mcp_tracker import get_mcp_status

router = APIRouter(tags=["doctor"])


def _request_base(request: Request) -> str:
    return f"{request.url.scheme}://{request.url.netloc}"


async def _owner_exists(session: AsyncSession) -> bool:
    result = await session.execute(select(User.id).where(User.role == "owner").limit(1))
    return result.scalar_one_or_none() is not None


async def _bootstrapped_projects(session: AsyncSession) -> list[str]:
    result = await session.execute(
        select(Memory.project)
        .where(
            Memory.source_type == "project_bootstrap",
            Memory.deleted_at.is_(None),
            Memory.project.is_not(None),
        )
        .group_by(Memory.project)
    )
    return [row[0] for row in result.fetchall() if row[0]]


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


async def _check_mcp(mcp_url: str) -> dict:
    try:
        import urllib.request
        import json as _json
        payload = _json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
        req = urllib.request.Request(
            mcp_url,
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            method="POST",
        )
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=3))
        return {"ok": True, "status_code": resp.status}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


@router.get("/api/system/doctor")
async def system_doctor(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Unauthenticated setup health check. Returns warnings and fix suggestions."""
    settings = get_settings()
    request_base = _request_base(request)
    profile = normalize_setup_profile(load_setup_profile())
    public_base = effective_public_url(request_base)
    auth_mode = settings._effective_auth_mode

    owner_exists = await _owner_exists(session)
    bootstrapped = await _bootstrapped_projects(session)
    mcp_url = f"{public_base}/mcp"
    mcp_check = await _check_mcp(mcp_url)
    mcp_status = get_mcp_status()

    port = settings.port
    port_ok = _port_in_use(port)

    parsed_public = urlparse(public_base)
    public_host = (parsed_public.hostname or "").lower()
    is_localhost_public = public_host in {"127.0.0.1", "localhost", "::1"}
    use_case = profile.get("use_case", "local_browser")

    warnings: list[dict] = []
    fix_suggestions: list[str] = []

    if not owner_exists:
        warnings.append({
            "code": "no_owner",
            "severity": "critical",
            "message": "No owner account exists. Open /setup or /oauth/authorize to create one.",
        })
        fix_suggestions.append("Open http://127.0.0.1:8787/setup in a browser to create the owner account and generate your API key.")

    if not bootstrapped:
        warnings.append({
            "code": "no_bootstrapped_projects",
            "severity": "warning",
            "message": "No projects have been bootstrapped. Run project_bootstrap from Cursor to index a repo.",
        })
        fix_suggestions.append("From Cursor, call: project_bootstrap(project='myproject', repo_path='/path/to/repo')")

    if use_case in {"ssh_remote", "headless", "remote_dev", "rpi5"} and is_localhost_public:
        warnings.append({
            "code": "public_url_localhost_remote",
            "severity": "warning",
            "message": f"MIMIR_PUBLIC_URL ({public_base}) points at localhost but the connection type is {use_case}. Remote Cursor clients cannot reach localhost.",
        })
        fix_suggestions.append("Update PUBLIC_URL to your LAN IP (e.g. http://192.168.1.246:8787) in /settings/connection or via MIMIR_PUBLIC_URL env var.")

    if auth_mode == "dev" and settings.env != "development":
        warnings.append({
            "code": "dev_auth_in_production",
            "severity": "critical",
            "message": "Auth mode is 'dev' but MIMIR_ENV is not 'development'. Set MIMIR_AUTH_MODE=single_user or multi_user.",
        })
        fix_suggestions.append("Set MIMIR_AUTH_MODE=single_user in your .env or docker-compose environment.")

    if not mcp_check["ok"]:
        warnings.append({
            "code": "mcp_unreachable",
            "severity": "warning",
            "message": f"MCP endpoint did not respond: {mcp_check.get('error', 'unknown')}",
        })

    status = "ok" if not any(w["severity"] == "critical" for w in warnings) else "needs_setup"
    if warnings and status == "ok":
        status = "warnings"

    return {
        "status": status,
        "version": __version__,
        "auth_mode": auth_mode,
        "public_url": public_base,
        "mcp_url": mcp_url,
        "web_url": f"{public_base}/",
        "database_mode": "postgres" if settings.database_url else "sqlite",
        "owner_exists": owner_exists,
        "bootstrapped_projects": bootstrapped,
        "port_listening": port_ok,
        "mcp_reachable": mcp_check["ok"],
        "mcp_last_connection": mcp_status,
        "warnings": warnings,
        "fix_suggestions": fix_suggestions,
        "setup_links": {
            "dashboard": f"{public_base}/",
            "connection_settings": f"{public_base}/settings/connection",
            "first_run_setup": f"{public_base}/setup",
            "projects": f"{public_base}/projects",
        },
        "checked_at": datetime.now(UTC).isoformat(),
    }
