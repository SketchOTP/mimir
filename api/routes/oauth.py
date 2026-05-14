"""OAuth 2.1 / PKCE endpoints + well-known discovery.

Supports three auth modes (MIMIR_AUTH_MODE):
  dev          — dev key bypass; OAuth disabled
  single_user  — one owner, local OAuth for Cursor
  multi_user   — full OAuth 2.1 with per-user tokens, registration disabled by default

Well-known endpoints (RFC 8414 / RFC 9728):
  GET /.well-known/oauth-protected-resource
  GET /.well-known/oauth-authorization-server

OAuth endpoints:
  POST /oauth/register    — dynamic client registration (RFC 7591)
  GET  /oauth/authorize   — show approval form (browser)
  POST /oauth/authorize   — process approval, redirect with code
  POST /oauth/token       — exchange code or refresh token for access token
  POST /oauth/revoke      — revoke an access or refresh token

PKCE (RFC 7636):
  code_challenge_method = S256 only (plain is insecure)
  code_verifier verified as BASE64URL(SHA256(verifier)) == code_challenge
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.parse
import uuid
from datetime import datetime, UTC, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mimir.config import get_settings
from mimir.setup_profile import build_mcp_config, effective_public_url, load_setup_profile, normalize_setup_profile, recommended_auth, save_setup_profile
from storage.database import get_session, get_session_factory
from storage.models import APIKey, OAuthAuthorizationCode, OAuthClient, OAuthRefreshToken, OAuthToken, User

router = APIRouter(tags=["oauth"])

# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _public_url(request: Request) -> str:
    """Derive the public base URL, falling back to the request origin."""
    request_base = f"{request.url.scheme}://{request.url.netloc}"
    return effective_public_url(request_base)


def _verify_pkce(code_verifier: str, code_challenge: str, method: str = "S256") -> bool:
    """Verify PKCE code_verifier against stored code_challenge."""
    if method != "S256":
        return False
    digest = hashlib.sha256(code_verifier.encode()).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return computed == code_challenge


def _require_oauth(request: Request) -> None:
    """Raise 404 if OAuth is explicitly disabled via MIMIR_OAUTH_ENABLED=false."""
    settings = get_settings()
    if not settings.oauth_enabled:
        raise HTTPException(status_code=404, detail="OAuth not enabled")


def _escape_attr(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _escape_html(value: str) -> str:
    return _escape_attr(value).replace("'", "&#39;")


def _auth_mode_label() -> str:
    settings = get_settings()
    if settings.is_single_user:
        return "single_user"
    if settings.is_multi_user:
        return "multi_user"
    return "dev"


# ── Well-known discovery ──────────────────────────────────────────────────────

@router.get("/.well-known/oauth-protected-resource")
async def well_known_resource(request: Request) -> JSONResponse:
    """RFC 9728: OAuth 2.0 Protected Resource Metadata."""
    base = _public_url(request)
    return JSONResponse(
        {
            "resource": base,
            "authorization_servers": [base],
            "bearer_methods_supported": ["header"],
            "resource_documentation": f"{base}/docs/OAUTH_SETUP.md",
        },
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/.well-known/oauth-authorization-server")
async def well_known_authorization_server(request: Request) -> JSONResponse:
    """RFC 8414: OAuth 2.0 Authorization Server Metadata."""
    base = _public_url(request)
    return JSONResponse(
        {
            "issuer": base,
            "authorization_endpoint": f"{base}/oauth/authorize",
            "token_endpoint": f"{base}/oauth/token",
            "registration_endpoint": f"{base}/oauth/register",
            "revocation_endpoint": f"{base}/oauth/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": ["mcp"],
        },
        headers={"Cache-Control": "public, max-age=3600"},
    )


# ── Dynamic client registration (RFC 7591) ────────────────────────────────────

class ClientRegistrationIn(BaseModel):
    redirect_uris: list[str]
    client_name: str | None = None
    grant_types: list[str] = ["authorization_code", "refresh_token"]
    response_types: list[str] = ["code"]
    token_endpoint_auth_method: str = "none"


@router.post("/oauth/register", status_code=status.HTTP_201_CREATED)
async def register_client(
    body: ClientRegistrationIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Dynamic client registration. Public clients (PKCE) only."""
    _require_oauth(request)

    if not body.redirect_uris:
        raise HTTPException(400, "redirect_uris required")

    client_id = f"mimir-{uuid.uuid4().hex[:16]}"
    client = OAuthClient(
        client_id=client_id,
        client_name=body.client_name,
        redirect_uris=json.dumps(body.redirect_uris),
        grant_types=json.dumps(body.grant_types),
        response_types=json.dumps(body.response_types),
        is_public=True,
    )
    session.add(client)
    await session.commit()

    base = _public_url(request)
    return JSONResponse(
        {
            "client_id": client_id,
            "client_name": body.client_name,
            "redirect_uris": body.redirect_uris,
            "grant_types": body.grant_types,
            "response_types": body.response_types,
            "token_endpoint_auth_method": "none",
            "registration_client_uri": f"{base}/oauth/register/{client_id}",
        },
        status_code=201,
    )


# ── Authorize form helpers ────────────────────────────────────────────────────

def _authorize_form(
    error: str = "",
    params: dict | None = None,
    *,
    api_key_value: str = "",
    info: str = "",
) -> str:
    params_hidden = ""
    if params:
        for k, v in params.items():
            params_hidden += f'<input type="hidden" name="{k}" value="{_escape_attr(v)}">\n'

    error_html = f'<p class="error">{error}</p>' if error else ""
    info_html = f'<p class="info">{info}</p>' if info else ""
    mode = _auth_mode_label()
    profile = normalize_setup_profile(load_setup_profile())
    profile_html = _connection_summary_html(profile, api_key="YOUR_API_KEY")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mimir — Authorize</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #0f1117; color: #e2e8f0;
          display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }}
  .card {{ background: #1a1d2e; border: 1px solid #2d3748; border-radius: 12px;
           padding: 2rem; max-width: 520px; width: 100%; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 0.5rem; color: #a78bfa; }}
  p {{ color: #94a3b8; margin: 0.5rem 0 1rem; font-size: 0.9rem; }}
  label {{ display: block; margin-bottom: 0.5rem; font-size: 0.85rem; color: #94a3b8; }}
  input[type=password], input[type=email], input[type=text] {{
    width: 100%; padding: 0.6rem 0.8rem; border-radius: 8px;
    border: 1px solid #2d3748; background: #0f1117; color: #e2e8f0;
    font-size: 0.9rem; box-sizing: border-box; margin-bottom: 1rem;
  }}
  .mode {{ display: inline-block; margin-bottom: 1rem; padding: 0.25rem 0.55rem; border-radius: 999px;
           background: #0f172a; border: 1px solid #334155; color: #cbd5e1; font-size: 0.78rem; }}
  .panel {{ background: #111827; border: 1px solid #374151; border-radius: 10px; padding: 0.9rem 1rem; margin: 1rem 0; }}
  .panel strong {{ color: #e2e8f0; }}
  .muted {{ color: #94a3b8; font-size: 0.85rem; }}
  .actions {{ display: flex; gap: 0.75rem; margin-top: 0.9rem; }}
  .actions a {{ flex: 1; text-align: center; border: 1px solid #334155; border-radius: 8px; color: #e2e8f0; text-decoration: none; padding: 0.6rem 0.8rem; font-size: 0.85rem; }}
  .actions a:hover {{ border-color: #64748b; }}
  button {{ width: 100%; padding: 0.7rem; border-radius: 8px; border: none;
            background: #7c3aed; color: white; font-size: 1rem; cursor: pointer; }}
  button:hover {{ background: #6d28d9; }}
  .error {{ color: #f87171; font-size: 0.85rem; margin-bottom: 1rem; }}
  .info {{ color: #93c5fd; font-size: 0.85rem; margin-bottom: 1rem; }}
  code {{ background: #0f1117; padding: .1rem .35rem; border-radius: 4px; }}
</style>
</head>
<body>
<div class="card">
  <h1>Mimir Access</h1>
  <div class="mode">Mode: {mode}</div>
  <p>This browser step authorizes Cursor to access this Mimir server.</p>
  <div class="panel">
    <strong>Which path should I use?</strong>
    <p class="muted">Browser-capable local Cursor can finish OAuth here. SSH, headless, and remote workflows can skip this page and connect with <code>Authorization: Bearer YOUR_API_KEY</code> directly.</p>
    <div class="actions">
      <a href="/">Open Dashboard</a>
      <a href="/settings/connection">Connection Setup</a>
    </div>
  </div>
  {profile_html}
  {error_html}
  {info_html}
  <form id="authorize-form" method="POST">
    {params_hidden}
    <label for="api_key">Your Mimir API Key</label>
    <input type="password" id="api_key" name="api_key" placeholder="paste your API key" value="{_escape_attr(api_key_value)}" required autocomplete="current-password">
    <button type="submit">Authorize Access</button>
  </form>
</div>
<script>
(() => {{
  const form = document.getElementById("authorize-form");
  if (!form) return;
  form.addEventListener("submit", () => {{
    try {{
      window.open("/", "_blank", "noopener");
    }} catch (_err) {{
      // noop
    }}
  }});
}})();
</script>
</body>
</html>"""


def _single_user_setup_form(error: str = "", email: str = "", display_name: str = "", params: dict | None = None) -> str:
    params_hidden = ""
    if params:
        for k, v in params.items():
            params_hidden += f'<input type="hidden" name="{k}" value="{_escape_attr(v)}">\n'
    error_html = f'<p class="error">{error}</p>' if error else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Mimir — First-Time Setup</title>
<style>
body{{font-family:system-ui,sans-serif;background:#0f1117;color:#e2e8f0;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0}}
.card{{background:#1a1d2e;border:1px solid #2d3748;border-radius:12px;padding:2rem;max-width:560px;width:100%}}
h1{{color:#a78bfa;margin-top:0}} p{{color:#94a3b8}} code{{background:#0f1117;padding:.2rem .5rem;border-radius:4px;font-size:.9rem}}
label{{display:block;margin-bottom:.5rem;font-size:.85rem;color:#94a3b8}}
input{{width:100%;padding:.6rem .8rem;border-radius:8px;border:1px solid #2d3748;background:#0f1117;color:#e2e8f0;font-size:.9rem;box-sizing:border-box;margin-bottom:1rem}}
button{{width:100%;padding:.7rem;border-radius:8px;border:none;background:#7c3aed;color:white;font-size:1rem;cursor:pointer}}
.error{{color:#f87171;font-size:.85rem;margin-bottom:1rem}}
.mode{{display:inline-block;margin-bottom:1rem;padding:.25rem .55rem;border-radius:999px;background:#0f172a;border:1px solid #334155;color:#cbd5e1;font-size:.78rem}}
.panel{{background:#111827;border:1px solid #374151;border-radius:10px;padding:.9rem 1rem;margin:1rem 0}}
.muted{{color:#94a3b8;font-size:.85rem}}
</style></head>
<body><div class="card">
<h1>Mimir First-Time Setup</h1>
<div class="mode">Mode: single_user</div>
<p>No owner account exists yet. This is a personal server, so you can create it right here.</p>
<div class="panel">
<strong>What happens next?</strong>
<p class="muted">Mimir will create your owner account, generate your first API key, show it once, and then let you finish Cursor authorization without leaving this page.</p>
</div>
{error_html}
<form method="POST">
{params_hidden}
<input type="hidden" name="setup_action" value="create_owner">
<label for="email">Email</label>
<input type="email" id="email" name="email" value="{_escape_attr(email)}" placeholder="you@example.com" required autocomplete="email">
<label for="display_name">Display name</label>
<input type="text" id="display_name" name="display_name" value="{_escape_attr(display_name)}" placeholder="Your Name" required autocomplete="name">
<button type="submit">Create Owner And Continue</button>
</form>
</div></body></html>"""


def _owner_created_page(raw_key: str, params: dict, email: str, display_name: str) -> str:
    profile = normalize_setup_profile(load_setup_profile())
    use_case = profile.get("use_case") or "local_browser"
    public_url = profile.get("public_url") or "http://127.0.0.1:8787"
    ssh_host = profile.get("ssh_host") or ""
    remote_mimir_path = profile.get("remote_mimir_path") or ""
    cursor_mcp_path = profile.get("cursor_mcp_path") or ""
    remote_python_path = profile.get("remote_python_path") or ""
    notes = profile.get("notes") or ""
    params_hidden = ""
    for k, v in params.items():
        params_hidden += f'<input type="hidden" name="{k}" value="{_escape_attr(v)}">\n'
    snippet = _escape_html(build_mcp_config({**profile, "public_url": public_url}, api_key=raw_key))
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Mimir — Owner Created</title>
<style>
body{{font-family:system-ui,sans-serif;background:#0f1117;color:#e2e8f0;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0}}
.card{{background:#1a1d2e;border:1px solid #2d3748;border-radius:12px;padding:2rem;max-width:560px;width:100%}}
h1{{color:#a78bfa;margin-top:0}} p{{color:#94a3b8}} code{{background:#0f1117;padding:.2rem .5rem;border-radius:4px;font-size:.9rem}}
textarea{{width:100%;min-height:96px;padding:.8rem;border-radius:8px;border:1px solid #2d3748;background:#0f1117;color:#e2e8f0;box-sizing:border-box;font-size:.92rem}}
button{{width:100%;padding:.7rem;border-radius:8px;border:none;background:#7c3aed;color:white;font-size:1rem;cursor:pointer;margin-top:1rem}}
.mode{{display:inline-block;margin-bottom:1rem;padding:.25rem .55rem;border-radius:999px;background:#0f172a;border:1px solid #334155;color:#cbd5e1;font-size:.78rem}}
.panel{{background:#111827;border:1px solid #374151;border-radius:10px;padding:.9rem 1rem;margin:1rem 0}}
.muted{{color:#94a3b8;font-size:.85rem}}
label{{display:block;margin:.85rem 0 .5rem;font-size:.85rem;color:#94a3b8}}
input,select{{width:100%;padding:.6rem .8rem;border-radius:8px;border:1px solid #2d3748;background:#0f1117;color:#e2e8f0;font-size:.9rem;box-sizing:border-box}}
pre{{background:#0f1117;padding:.8rem;border-radius:8px;overflow:auto;color:#cbd5e1;font-size:.82rem}}
</style></head>
<body><div class="card">
<h1>Owner Created</h1>
<div class="mode">Mode: single_user</div>
<p><strong>{_escape_html(display_name)}</strong> was created as the owner for <strong>{_escape_html(email)}</strong>.</p>
<div class="panel">
<strong>Your API key</strong>
<p class="muted">This is the only time Mimir will show this key. Keep it for SSH, headless, or direct Bearer-auth setups.</p>
<textarea readonly>{_escape_html(raw_key)}</textarea>
</div>
<form method="POST">
{params_hidden}
<input type="hidden" name="setup_action" value="save_profile_and_authorize">
<input type="hidden" name="api_key" value="{_escape_attr(raw_key)}">
<label for="use_case">Connection type</label>
<select id="use_case" name="use_case">
  <option value="local_browser"{" selected" if use_case == "local_browser" else ""}>Local Cursor with browser</option>
  <option value="lan_browser"{" selected" if use_case == "lan_browser" else ""}>LAN browser access</option>
  <option value="ssh_remote"{" selected" if use_case == "ssh_remote" else ""}>Cursor over SSH</option>
  <option value="remote_dev"{" selected" if use_case == "remote_dev" else ""}>Remote development box</option>
  <option value="headless"{" selected" if use_case == "headless" else ""}>Headless client</option>
  <option value="rpi5"{" selected" if use_case == "rpi5" else ""}>RPi5 workflow</option>
  <option value="hosted_https"{" selected" if use_case == "hosted_https" else ""}>Hosted HTTPS server</option>
</select>
<label for="public_url">Public/base URL for this Mimir server</label>
<input type="text" id="public_url" name="public_url" value="{_escape_attr(public_url)}" placeholder="http://127.0.0.1:8787">
<label for="ssh_host">SSH host alias (optional)</label>
<input type="text" id="ssh_host" name="ssh_host" value="{_escape_attr(ssh_host)}" placeholder="my-box">
<label for="remote_mimir_path">Remote Mimir path (optional)</label>
<input type="text" id="remote_mimir_path" name="remote_mimir_path" value="{_escape_attr(remote_mimir_path)}" placeholder="/home/user/Projects/mimir">
<label for="remote_python_path">Remote Python / venv path (optional)</label>
<input type="text" id="remote_python_path" name="remote_python_path" value="{_escape_attr(remote_python_path)}" placeholder="/home/user/Projects/mimir/.venv/bin/python">
<label for="cursor_mcp_path">Cursor MCP config path (optional)</label>
<input type="text" id="cursor_mcp_path" name="cursor_mcp_path" value="{_escape_attr(cursor_mcp_path)}" placeholder="~/.cursor/mcp.json">
<label for="notes">Setup notes (optional)</label>
<input type="text" id="notes" name="notes" value="{_escape_attr(notes)}" placeholder="Anything specific about this machine or workflow">
<div class="panel">
<strong>Recommended MCP config</strong>
<p class="muted">This is generated from the connection type and URL above. SSH/headless-style profiles use Bearer auth directly.</p>
<pre>{snippet}</pre>
</div>
<button type="submit">Save Setup And Authorize Cursor</button>
</form>
</div></body></html>"""


def _setup_required_page() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Mimir — Setup Required</title>
<style>body{font-family:system-ui,sans-serif;background:#0f1117;color:#e2e8f0;
display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0}
.card{background:#1a1d2e;border:1px solid #2d3748;border-radius:12px;padding:2rem;max-width:560px}
h1{color:#a78bfa}code{background:#0f1117;padding:.2rem .5rem;border-radius:4px;font-size:.9rem}
.mode{display:inline-block;margin-bottom:1rem;padding:.25rem .55rem;border-radius:999px;background:#0f172a;border:1px solid #334155;color:#cbd5e1;font-size:.78rem}
.panel{background:#111827;border:1px solid #374151;border-radius:10px;padding:.9rem 1rem;margin:1rem 0}
.muted{color:#94a3b8;font-size:.85rem}
</style></head>
<body><div class="card">
<h1>Mimir Setup Required</h1>
<div class="mode">Mode: multi_user</div>
<p>This server is running in multi-user mode and does not allow browser-first owner creation.</p>
<div class="panel">
<strong>Server operator action required</strong>
<p class="muted">Create the first owner account on the server, then come back and sign in here with that API key.</p>
</div>
<pre><code>python -m mimir.auth.create_owner --email you@example.com --display-name "Your Name"</code></pre>
<p>For SSH or headless clients, you can also skip browser OAuth and connect Cursor with <code>Authorization: Bearer YOUR_API_KEY</code>.</p>
<div class="panel">
<strong>Prefer guided setup?</strong>
<p class="muted">Open the dashboard connection flow to walk through profile setup and generated MCP config.</p>
<p><a href="/" style="color:#c4b5fd">Open Mimir Dashboard</a> · <a href="/settings/connection" style="color:#c4b5fd">Open Connection Setup</a></p>
</div>
</div></body></html>"""


async def _create_owner_account(session: AsyncSession, email: str, display_name: str) -> tuple[User, str]:
    existing = await session.execute(select(User).where(User.role == "owner").limit(1))
    if existing.scalar_one_or_none():
        raise HTTPException(409, "An owner account already exists.")

    user = User(
        id=uuid.uuid4().hex,
        email=email,
        display_name=display_name,
        role="owner",
    )
    session.add(user)

    raw_key = secrets.token_urlsafe(32)
    api_key = APIKey(
        id=uuid.uuid4().hex,
        user_id=user.id,
        key_hash=_hash(raw_key),
        name="default",
    )
    session.add(api_key)
    await session.commit()
    return user, raw_key


def _connection_summary_html(profile: dict[str, Any], api_key: str) -> str:
    if not any(profile.values()):
        return ""
    snippet = _escape_html(build_mcp_config(profile, api_key=api_key))
    auth = recommended_auth(profile.get("use_case") or "local_browser")
    extras = []
    if profile.get("ssh_host"):
        extras.append(f"<div><span class='muted'>SSH host:</span> <code>{_escape_html(profile['ssh_host'])}</code></div>")
    if profile.get("remote_mimir_path"):
        extras.append(f"<div><span class='muted'>Remote path:</span> <code>{_escape_html(profile['remote_mimir_path'])}</code></div>")
    if profile.get("cursor_mcp_path"):
        extras.append(f"<div><span class='muted'>Cursor MCP path:</span> <code>{_escape_html(profile['cursor_mcp_path'])}</code></div>")
    return (
        "<div class='panel'>"
        "<strong>Saved connection profile</strong>"
        f"<p class='muted'>Use case: <code>{_escape_html(profile.get('use_case') or 'local_browser')}</code> · Recommended auth: <code>{auth}</code></p>"
        f"{''.join(extras)}"
        f"<pre><code>{snippet}</code></pre>"
        "</div>"
    )


# ── Authorize endpoint ─────────────────────────────────────────────────────────

async def _validate_auth_request(
    session: AsyncSession,
    client_id: str,
    redirect_uri: str,
    response_type: str,
    code_challenge: str,
    code_challenge_method: str,
) -> OAuthClient:
    """Validate OAuth authorization request parameters; return client."""
    if response_type != "code":
        raise HTTPException(400, "response_type must be 'code'")
    if code_challenge_method not in ("S256",):
        raise HTTPException(400, "code_challenge_method must be S256")
    if not code_challenge:
        raise HTTPException(400, "code_challenge required (PKCE S256)")

    client = await session.get(OAuthClient, client_id)
    if not client or not client.is_active:
        raise HTTPException(400, "Unknown client_id")

    allowed_uris = json.loads(client.redirect_uris)
    if redirect_uri not in allowed_uris:
        raise HTTPException(400, f"redirect_uri not registered for this client")

    return client


@router.get("/oauth/authorize")
async def authorize_get(
    request: Request,
    response_type: str = "",
    client_id: str = "",
    redirect_uri: str = "",
    state: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "S256",
    scope: str = "",
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Show the OAuth authorization form in the user's browser."""
    _require_oauth(request)
    settings = get_settings()

    # Check if any owner exists (first-run guard)
    result = await session.execute(select(User).where(User.role == "owner").limit(1))
    if not result.scalar_one_or_none():
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
            "scope": scope,
        }
        if settings.is_single_user:
            return HTMLResponse(_single_user_setup_form(params=params), status_code=200)
        return HTMLResponse(_setup_required_page(), status_code=200)

    # Validate params (raise early if obviously wrong)
    try:
        await _validate_auth_request(
            session, client_id, redirect_uri, response_type, code_challenge, code_challenge_method
        )
    except HTTPException as exc:
        return HTMLResponse(f"<h1>Error</h1><p>{exc.detail}</p>", status_code=400)

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "scope": scope,
    }
    return HTMLResponse(_authorize_form(params=params))


@router.post("/oauth/authorize")
async def authorize_post(
    request: Request,
    client_id: str = Form(""),
    redirect_uri: str = Form(""),
    state: str = Form(""),
    code_challenge: str = Form(""),
    code_challenge_method: str = Form("S256"),
    scope: str = Form(""),
    setup_action: str = Form(""),
    email: str = Form(""),
    display_name: str = Form(""),
    use_case: str = Form("local_browser"),
    public_url: str = Form(""),
    ssh_host: str = Form(""),
    remote_mimir_path: str = Form(""),
    cursor_mcp_path: str = Form(""),
    remote_python_path: str = Form(""),
    notes: str = Form(""),
    api_key: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Process OAuth authorization form submission."""
    _require_oauth(request)

    # Validate auth request
    try:
        await _validate_auth_request(
            session, client_id, redirect_uri, "code", code_challenge, code_challenge_method
        )
    except HTTPException as exc:
        return HTMLResponse(f"<h1>Error</h1><p>{exc.detail}</p>", status_code=400)

    params = {
        "client_id": client_id, "redirect_uri": redirect_uri, "state": state,
        "code_challenge": code_challenge, "code_challenge_method": code_challenge_method, "scope": scope,
    }
    settings = get_settings()

    owner_exists = (
        await session.execute(select(User).where(User.role == "owner").limit(1))
    ).scalar_one_or_none() is not None
    if not owner_exists:
        if settings.is_single_user and setup_action == "create_owner":
            if not email.strip() or not display_name.strip():
                return HTMLResponse(
                    _single_user_setup_form(
                        error="Email and display name are required.",
                        email=email,
                        display_name=display_name,
                        params=params,
                    )
                )
            try:
                _, raw_key = await _create_owner_account(session, email.strip(), display_name.strip())
            except HTTPException as exc:
                return HTMLResponse(
                    _single_user_setup_form(
                        error=str(exc.detail),
                        email=email,
                        display_name=display_name,
                        params=params,
                    ),
                    status_code=409,
                )
            return HTMLResponse(_owner_created_page(raw_key, params, email.strip(), display_name.strip()))

        if settings.is_single_user:
            return HTMLResponse(_single_user_setup_form(params=params), status_code=200)
        return HTMLResponse(_setup_required_page(), status_code=200)

    if setup_action == "save_profile_and_authorize":
        save_setup_profile({
            "use_case": use_case,
            "public_url": public_url,
            "ssh_host": ssh_host,
            "remote_mimir_path": remote_mimir_path,
            "cursor_mcp_path": cursor_mcp_path,
            "remote_python_path": remote_python_path,
            "notes": notes,
        })

    # Authenticate user via API key
    if not api_key:
        return HTMLResponse(_authorize_form(error="API key required.", params=params))
    user = None

    # Legacy single-key match
    if api_key == settings.api_key and settings.api_key != settings.dev_api_key:
        # Only allow in single_user mode for the admin user
        if settings.is_single_user:
            result = await session.execute(select(User).where(User.role == "owner").limit(1))
            user = result.scalar_one_or_none()

    if not user:
        key_hash = _hash(api_key)
        result = await session.execute(
            select(APIKey).where(APIKey.key_hash == key_hash, APIKey.is_active == True)  # noqa: E712
        )
        key_row = result.scalar_one_or_none()
        if not key_row:
            return HTMLResponse(_authorize_form(error="Invalid API key.", params=params))
        user = await session.get(User, key_row.user_id)

    if not user or not user.is_active:
        return HTMLResponse(_authorize_form(error="Account is inactive.", params=params))

    # Generate authorization code
    code = secrets.token_urlsafe(32)
    expires_at = _now() + timedelta(seconds=300)  # 5 min

    auth_code = OAuthAuthorizationCode(
        code=code,
        client_id=client_id,
        user_id=user.id,
        redirect_uri=redirect_uri,
        scope=scope or "mcp",
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        expires_at=expires_at,
    )
    session.add(auth_code)

    # Update last_login_at
    user.last_login_at = _now()
    await session.commit()

    # Redirect back to client with code
    qs = {"code": code}
    if state:
        qs["state"] = state
    redirect_url = f"{redirect_uri}?{urllib.parse.urlencode(qs)}"
    return RedirectResponse(redirect_url, status_code=302)


# ── Token endpoint ────────────────────────────────────────────────────────────

def _token_error(error: str, description: str) -> JSONResponse:
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=400,
        headers={"Cache-Control": "no-store"},
    )


def _token_response(access_token: str, expires_in: int, refresh_token: str | None = None) -> JSONResponse:
    body: dict[str, Any] = {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "scope": "mcp",
    }
    if refresh_token:
        body["refresh_token"] = refresh_token
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


async def _issue_tokens(
    session: AsyncSession,
    client_id: str,
    user_id: str,
    scope: str,
) -> tuple[str, str]:
    """Create and store an access token + refresh token. Returns (raw_access, raw_refresh)."""
    settings = get_settings()
    now = _now()

    raw_access = secrets.token_urlsafe(32)
    access_hash = _hash(raw_access)
    access_expires = now + timedelta(seconds=settings.access_token_ttl_seconds)

    access_tok = OAuthToken(
        token_hash=access_hash,
        client_id=client_id,
        user_id=user_id,
        scope=scope,
        expires_at=access_expires,
    )
    session.add(access_tok)

    raw_refresh = secrets.token_urlsafe(32)
    refresh_hash = _hash(raw_refresh)
    refresh_expires = now + timedelta(seconds=settings.refresh_token_ttl_seconds)

    refresh_tok = OAuthRefreshToken(
        token_hash=refresh_hash,
        access_token_hash=access_hash,
        client_id=client_id,
        user_id=user_id,
        scope=scope,
        expires_at=refresh_expires,
    )
    session.add(refresh_tok)
    await session.commit()

    return raw_access, raw_refresh


@router.post("/oauth/token")
async def token(
    request: Request,
    grant_type: str = Form(""),
    code: str = Form(""),
    redirect_uri: str = Form(""),
    client_id: str = Form(""),
    code_verifier: str = Form(""),
    refresh_token: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Exchange authorization code or refresh token for access token."""
    _require_oauth(request)

    settings = get_settings()

    if grant_type == "authorization_code":
        if not code:
            return _token_error("invalid_request", "code required")

        # Look up the auth code
        code_row = await session.get(OAuthAuthorizationCode, code)
        if not code_row:
            return _token_error("invalid_grant", "Authorization code not found")
        if code_row.used:
            return _token_error("invalid_grant", "Authorization code already used")
        now = _now()
        expires_naive = code_row.expires_at.replace(tzinfo=None) if code_row.expires_at.tzinfo else code_row.expires_at
        if now > expires_naive:
            return _token_error("invalid_grant", "Authorization code expired")
        if client_id and code_row.client_id != client_id:
            return _token_error("invalid_grant", "client_id mismatch")
        if redirect_uri and code_row.redirect_uri != redirect_uri:
            return _token_error("invalid_grant", "redirect_uri mismatch")

        # Verify PKCE
        if not code_verifier:
            return _token_error("invalid_request", "code_verifier required")
        if not _verify_pkce(code_verifier, code_row.code_challenge, code_row.code_challenge_method):
            return _token_error("invalid_grant", "PKCE verification failed")

        # Mark code as used (one-time use)
        code_row.used = True
        await session.flush()

        raw_access, raw_refresh = await _issue_tokens(
            session, code_row.client_id, code_row.user_id, code_row.scope or "mcp"
        )
        return _token_response(raw_access, settings.access_token_ttl_seconds, raw_refresh)

    elif grant_type == "refresh_token":
        if not refresh_token:
            return _token_error("invalid_request", "refresh_token required")

        rt_hash = _hash(refresh_token)
        result = await session.execute(
            select(OAuthRefreshToken).where(
                OAuthRefreshToken.token_hash == rt_hash,
                OAuthRefreshToken.revoked == False,  # noqa: E712
            )
        )
        rt_row = result.scalar_one_or_none()
        if not rt_row:
            return _token_error("invalid_grant", "Refresh token not found or revoked")

        now = _now()
        if rt_row.expires_at:
            exp = rt_row.expires_at.replace(tzinfo=None) if rt_row.expires_at.tzinfo else rt_row.expires_at
            if now > exp:
                return _token_error("invalid_grant", "Refresh token expired")

        # Revoke old refresh token (rotation)
        rt_row.revoked = True
        # Revoke old access token too
        if rt_row.access_token_hash:
            old_at = await session.get(OAuthToken, rt_row.access_token_hash)
            if old_at:
                old_at.revoked = True

        await session.flush()

        raw_access, raw_refresh = await _issue_tokens(
            session, rt_row.client_id, rt_row.user_id, rt_row.scope or "mcp"
        )
        return _token_response(raw_access, settings.access_token_ttl_seconds, raw_refresh)

    else:
        return _token_error("unsupported_grant_type", f"Unsupported grant_type: {grant_type!r}")


# ── Revocation endpoint ───────────────────────────────────────────────────────

@router.post("/oauth/revoke")
async def revoke(
    request: Request,
    token: str = Form(""),
    token_type_hint: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Revoke an access or refresh token (RFC 7009). Always returns 200."""
    _require_oauth(request)

    if not token:
        return Response(status_code=200)

    tok_hash = _hash(token)

    # Try access token first
    at = await session.get(OAuthToken, tok_hash)
    if at:
        at.revoked = True
        await session.commit()
        return Response(status_code=200)

    # Try refresh token
    rt = await session.get(OAuthRefreshToken, tok_hash)
    if rt:
        rt.revoked = True
        await session.commit()
        return Response(status_code=200)

    # Token not found — still return 200 per RFC 7009
    return Response(status_code=200)


# ── Token resolution (used by deps.py + mcp_http.py) ─────────────────────────

async def resolve_oauth_token(token_value: str) -> str | None:
    """Validate an OAuth Bearer token; return user_id or None if invalid/unknown.

    Returns:
        user_id  — valid active token
        None     — token not in DB, revoked, or expired
    """
    tok_hash = _hash(token_value)
    factory = get_session_factory()
    async with factory() as session:
        at = await session.get(OAuthToken, tok_hash)
        if not at or at.revoked:
            return None
        if at.expires_at:
            now = _now()
            exp = at.expires_at.replace(tzinfo=None) if at.expires_at.tzinfo else at.expires_at
            if now > exp:
                return None
        return at.user_id


async def is_revoked_oauth_token(token_value: str) -> bool:
    """Return True if the token exists in the DB but is revoked/expired (not just unknown)."""
    tok_hash = _hash(token_value)
    factory = get_session_factory()
    async with factory() as session:
        at = await session.get(OAuthToken, tok_hash)
        if at is None:
            return False
        # Token exists in DB → it was an OAuth token; check validity
        if at.revoked:
            return True
        if at.expires_at:
            now = _now()
            exp = at.expires_at.replace(tzinfo=None) if at.expires_at.tzinfo else at.expires_at
            if now > exp:
                return True
        return False


# ── Setup page (first-run) ────────────────────────────────────────────────────

@router.get("/setup")
async def setup_page(session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    """First-run setup page — shown when no owner exists."""
    result = await session.execute(select(User).where(User.role == "owner").limit(1))
    if result.scalar_one_or_none():
        return HTMLResponse(
            "<h1>Setup already complete</h1><p>An owner account exists. <a href='/'>Go to Mimir</a></p>",
            status_code=200,
        )
    settings = get_settings()
    if settings.is_single_user:
        return HTMLResponse(_single_user_setup_form(), status_code=200)
    return HTMLResponse(_setup_required_page(), status_code=200)
