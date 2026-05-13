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
    settings = get_settings()
    if settings.public_url:
        return settings.public_url.rstrip("/")
    return f"{request.url.scheme}://{request.url.netloc}"


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

def _authorize_form(error: str = "", params: dict | None = None) -> str:
    """Minimal HTML authorize page."""
    params_hidden = ""
    if params:
        for k, v in params.items():
            params_hidden += f'<input type="hidden" name="{k}" value="{v}">\n'

    error_html = f'<p class="error">{error}</p>' if error else ""
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
           padding: 2rem; max-width: 400px; width: 100%; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 0.5rem; color: #a78bfa; }}
  p {{ color: #94a3b8; margin: 0.5rem 0 1.5rem; font-size: 0.9rem; }}
  label {{ display: block; margin-bottom: 0.5rem; font-size: 0.85rem; color: #94a3b8; }}
  input[type=password], input[type=email] {{
    width: 100%; padding: 0.6rem 0.8rem; border-radius: 8px;
    border: 1px solid #2d3748; background: #0f1117; color: #e2e8f0;
    font-size: 0.9rem; box-sizing: border-box; margin-bottom: 1rem;
  }}
  button {{ width: 100%; padding: 0.7rem; border-radius: 8px; border: none;
            background: #7c3aed; color: white; font-size: 1rem; cursor: pointer; }}
  button:hover {{ background: #6d28d9; }}
  .error {{ color: #f87171; font-size: 0.85rem; margin-bottom: 1rem; }}
</style>
</head>
<body>
<div class="card">
  <h1>🧠 Mimir</h1>
  <p>An application is requesting access to your Mimir memory. Enter your API key to authorize.</p>
  {error_html}
  <form method="POST">
    {params_hidden}
    <label for="api_key">Your Mimir API Key</label>
    <input type="password" id="api_key" name="api_key" placeholder="paste your API key" required autocomplete="current-password">
    <button type="submit">Authorize Access</button>
  </form>
</div>
</body>
</html>"""


def _setup_required_page() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Mimir — Setup Required</title>
<style>body{font-family:system-ui,sans-serif;background:#0f1117;color:#e2e8f0;
display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0}
.card{background:#1a1d2e;border:1px solid #2d3748;border-radius:12px;padding:2rem;max-width:480px}
h1{color:#a78bfa}code{background:#0f1117;padding:.2rem .5rem;border-radius:4px;font-size:.9rem}
</style></head>
<body><div class="card">
<h1>🧠 Mimir — Setup Required</h1>
<p>No owner account exists yet. Create one with:</p>
<pre><code>python -m mimir.auth.create_owner --email you@example.com --display-name "Your Name"</code></pre>
<p>This will print your API key. Then reload this page and authorize.</p>
</div></body></html>"""


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

    # Check if any owner exists (first-run guard)
    result = await session.execute(select(User).where(User.role == "owner").limit(1))
    if not result.scalar_one_or_none():
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

    # Authenticate user via API key
    if not api_key:
        return HTMLResponse(_authorize_form(error="API key required.", params=params))

    settings = get_settings()
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
    return HTMLResponse(_setup_required_page(), status_code=200)
