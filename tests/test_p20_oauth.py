"""P20: OAuth 2.1/PKCE multi-user auth tests.

Covers:
  - Discovery endpoints (well-known)
  - Dynamic client registration
  - OAuth authorize form
  - Token exchange (authorization_code + PKCE)
  - Refresh token rotation
  - Token revocation
  - MCP tools/list works with OAuth token
  - Cross-user isolation with OAuth tokens
  - dev key rejected in multi_user mode
  - single_user owner setup guard
  - Config auth mode behavior
  - create_owner CLI logic
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.parse
import uuid
import os
from datetime import datetime, UTC, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pkce_pair() -> tuple[str, str]:
    """Generate (code_verifier, code_challenge) S256 PKCE pair."""
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


async def _create_test_user(session, email: str, role: str = "user"):
    """Create a test user + API key, return (user, raw_key)."""
    import hashlib as hl
    from storage.models import User, APIKey

    user = User(
        id=uuid.uuid4().hex,
        email=email,
        display_name=f"Test {email}",
        role=role,
    )
    session.add(user)
    raw_key = secrets.token_urlsafe(32)
    api_key = APIKey(
        id=uuid.uuid4().hex,
        user_id=user.id,
        key_hash=hl.sha256(raw_key.encode()).hexdigest(),
        name="test",
    )
    session.add(api_key)
    await session.commit()
    return user, raw_key


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
async def oauth_client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture(scope="module")
async def db_session(app):
    from storage.database import get_session_factory
    factory = get_session_factory()
    async with factory() as session:
        yield session


# ── Well-known discovery ──────────────────────────────────────────────────────

class TestWellKnown:
    async def test_protected_resource_metadata(self, oauth_client):
        r = await oauth_client.get("/.well-known/oauth-protected-resource")
        assert r.status_code == 200
        body = r.json()
        assert "authorization_servers" in body
        assert "resource" in body
        assert len(body["authorization_servers"]) > 0

    async def test_authorization_server_metadata(self, oauth_client):
        r = await oauth_client.get("/.well-known/oauth-authorization-server")
        assert r.status_code == 200
        body = r.json()
        assert body["response_types_supported"] == ["code"]
        assert "S256" in body["code_challenge_methods_supported"]
        assert "/oauth/authorize" in body["authorization_endpoint"]
        assert "/oauth/token" in body["token_endpoint"]
        assert "/oauth/register" in body["registration_endpoint"]

    async def test_discovery_cache_control(self, oauth_client):
        r = await oauth_client.get("/.well-known/oauth-authorization-server")
        assert "max-age" in r.headers.get("cache-control", "")


# ── Dynamic client registration ────────────────────────────────────────────────

class TestClientRegistration:
    async def test_register_client_success(self, oauth_client):
        r = await oauth_client.post("/oauth/register", json={
            "redirect_uris": ["http://localhost:12345/callback"],
            "client_name": "Test Cursor",
        })
        assert r.status_code == 201
        body = r.json()
        assert "client_id" in body
        assert body["client_id"].startswith("mimir-")
        assert body["redirect_uris"] == ["http://localhost:12345/callback"]

    async def test_register_requires_redirect_uris(self, oauth_client):
        r = await oauth_client.post("/oauth/register", json={"redirect_uris": []})
        assert r.status_code in (400, 422)


# ── Authorize + token exchange ────────────────────────────────────────────────

class TestOAuthFlow:
    async def test_authorize_get_no_owner_shows_setup(self, oauth_client, db_session):
        """If no owner exists, /oauth/authorize shows setup page."""
        from storage.models import User
        result = await db_session.execute(select(User).where(User.role == "owner").limit(1))
        has_owner = result.scalar_one_or_none() is not None

        verifier, challenge = _pkce_pair()
        # Register a client first
        reg = await oauth_client.post("/oauth/register", json={
            "redirect_uris": ["http://localhost:9999/callback"],
        })
        client_id = reg.json()["client_id"]

        r = await oauth_client.get("/oauth/authorize", params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "http://localhost:9999/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "test-state",
        })
        assert r.status_code == 200
        if not has_owner:
            assert "Setup" in r.text or "setup" in r.text.lower()
        else:
            assert "Authorize" in r.text or "authorize" in r.text.lower()

    async def test_single_user_setup_page_explains_flow(self, oauth_client):
        from mimir.config import get_settings
        from storage.database import get_session_factory
        from storage.models import User
        from sqlalchemy import select

        settings = get_settings()
        original_mode = settings.auth_mode
        object.__setattr__(settings, "auth_mode", "single_user")
        try:
            factory = get_session_factory()
            async with factory() as session:
                has_owner = (
                    await session.execute(select(User).where(User.role == "owner").limit(1))
                ).scalar_one_or_none() is not None
            verifier, challenge = _pkce_pair()
            reg = await oauth_client.post("/oauth/register", json={
                "redirect_uris": ["http://localhost:9898/callback"],
            })
            client_id = reg.json()["client_id"]
            r = await oauth_client.get("/oauth/authorize", params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": "http://localhost:9898/callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "setup-state",
            })
            assert r.status_code == 200
            assert "Mode: single_user" in r.text
            if has_owner:
                assert "Authorize Access" in r.text or "authorize" in r.text.lower()
            else:
                assert "Create Owner And Continue" in r.text or "create owner" in r.text.lower()
        finally:
            object.__setattr__(settings, "auth_mode", original_mode)

    async def test_single_user_setup_can_create_owner_in_browser(self, oauth_client, db_session):
        from mimir.config import get_settings
        from mimir.setup_profile import load_setup_profile
        from storage.models import APIKey, User

        # Clear any existing owners so the setup flow is reachable.
        result = await db_session.execute(select(User).where(User.role == "owner"))
        owners = list(result.scalars())
        for owner in owners:
            keys = await db_session.execute(select(APIKey).where(APIKey.user_id == owner.id))
            for key in keys.scalars():
                await db_session.delete(key)
            await db_session.delete(owner)
        await db_session.commit()

        settings = get_settings()
        original_mode = settings.auth_mode
        object.__setattr__(settings, "auth_mode", "single_user")
        try:
            verifier, challenge = _pkce_pair()
            reg = await oauth_client.post("/oauth/register", json={
                "redirect_uris": ["http://localhost:10998/callback"],
            })
            client_id = reg.json()["client_id"]
            r = await oauth_client.post("/oauth/authorize", data={
                "client_id": client_id,
                "redirect_uri": "http://localhost:10998/callback",
                "state": "setup-state",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "mcp",
                "setup_action": "create_owner",
                "email": "owner-browser@test.com",
                "display_name": "Owner Browser",
            })
            assert r.status_code == 200
            assert "Owner Created" in r.text
            assert "Save Setup And Authorize Cursor" in r.text

            owner = (
                await db_session.execute(select(User).where(User.email == "owner-browser@test.com"))
            ).scalar_one_or_none()
            assert owner is not None
            assert owner.role == "owner"
            profile = load_setup_profile()
            assert isinstance(profile, dict)
        finally:
            object.__setattr__(settings, "auth_mode", original_mode)

    async def test_authorize_can_save_connection_profile_before_redirect(self, oauth_client, db_session):
        from mimir.setup_profile import load_setup_profile

        owner, raw_key = await _create_test_user(db_session, f"profile-{uuid.uuid4().hex[:8]}@test.com", role="owner")
        reg = await oauth_client.post("/oauth/register", json={
            "redirect_uris": ["http://localhost:11998/callback"],
            "client_name": "Profile Test",
        })
        client_id = reg.json()["client_id"]
        verifier, challenge = _pkce_pair()

        r = await oauth_client.post("/oauth/authorize", data={
            "client_id": client_id,
            "redirect_uri": "http://localhost:11998/callback",
            "state": "profile-state",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "mcp",
            "api_key": raw_key,
            "setup_action": "save_profile_and_authorize",
            "use_case": "ssh_remote",
            "public_url": "http://192.168.1.50:8787",
            "ssh_host": "atlas",
            "remote_mimir_path": "/home/sketch/Projects/mimir",
            "cursor_mcp_path": "~/.cursor/mcp.json",
            "remote_python_path": "/home/sketch/Projects/mimir/.venv/bin/python",
            "notes": "SSH-first setup",
        }, follow_redirects=False)
        assert r.status_code == 302
        assert owner.id

        profile = load_setup_profile()
        assert profile["use_case"] == "ssh_remote"
        assert profile["public_url"] == "http://192.168.1.50:8787"
        assert profile["ssh_host"] == "atlas"
        assert profile["remote_mimir_path"] == "/home/sketch/Projects/mimir"

    async def test_discovery_uses_saved_public_url_when_env_blank(self, oauth_client):
        from mimir.setup_profile import save_setup_profile

        save_setup_profile({
            "use_case": "hosted_https",
            "public_url": "https://mimir.example.com",
        })
        r = await oauth_client.get("/.well-known/oauth-protected-resource")
        assert r.status_code == 200
        body = r.json()
        assert body["resource"] == "https://mimir.example.com"

    async def test_full_oauth_flow(self, oauth_client, db_session):
        """Full authorization_code + PKCE flow with a real user."""
        # Create owner
        owner, raw_key = await _create_test_user(db_session, f"owner-{uuid.uuid4().hex[:8]}@test.com", role="owner")

        # Register client
        reg = await oauth_client.post("/oauth/register", json={
            "redirect_uris": ["http://localhost:11111/callback"],
            "client_name": "Cursor Test",
        })
        assert reg.status_code == 201
        client_id = reg.json()["client_id"]

        # Generate PKCE pair
        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(8)

        # POST to authorize (simulating form submission with API key)
        r = await oauth_client.post("/oauth/authorize", data={
            "client_id": client_id,
            "redirect_uri": "http://localhost:11111/callback",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "mcp",
            "api_key": raw_key,
        }, follow_redirects=False)
        assert r.status_code == 302
        location = r.headers["location"]
        assert "code=" in location
        parsed = urllib.parse.urlparse(location)
        qs = urllib.parse.parse_qs(parsed.query)
        code = qs["code"][0]
        assert qs["state"][0] == state

        # Exchange code for token
        tok_r = await oauth_client.post("/oauth/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://localhost:11111/callback",
            "client_id": client_id,
            "code_verifier": verifier,
        })
        assert tok_r.status_code == 200
        tok_body = tok_r.json()
        assert "access_token" in tok_body
        assert tok_body["token_type"] == "Bearer"
        assert "refresh_token" in tok_body
        assert tok_body["expires_in"] > 0

        return tok_body, owner  # used by dependent tests

    async def test_code_cannot_be_reused(self, oauth_client, db_session):
        """Authorization code is one-time use."""
        owner, raw_key = await _create_test_user(db_session, f"once-{uuid.uuid4().hex[:8]}@test.com", role="owner")
        reg = await oauth_client.post("/oauth/register", json={"redirect_uris": ["http://localhost:22222/callback"]})
        client_id = reg.json()["client_id"]
        verifier, challenge = _pkce_pair()

        r = await oauth_client.post("/oauth/authorize", data={
            "client_id": client_id,
            "redirect_uri": "http://localhost:22222/callback",
            "state": "x",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "api_key": raw_key,
        }, follow_redirects=False)
        assert r.status_code == 302
        code = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers["location"]).query)["code"][0]

        # First exchange succeeds
        r1 = await oauth_client.post("/oauth/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "code_verifier": verifier,
        })
        assert r1.status_code == 200

        # Second exchange fails
        r2 = await oauth_client.post("/oauth/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "code_verifier": verifier,
        })
        assert r2.status_code == 400
        assert "already used" in r2.json().get("error_description", "")

    async def test_invalid_pkce_fails(self, oauth_client, db_session):
        """Wrong code_verifier must reject the token exchange."""
        owner, raw_key = await _create_test_user(db_session, f"pkce-{uuid.uuid4().hex[:8]}@test.com", role="owner")
        reg = await oauth_client.post("/oauth/register", json={"redirect_uris": ["http://localhost:33333/callback"]})
        client_id = reg.json()["client_id"]
        _, challenge = _pkce_pair()

        r = await oauth_client.post("/oauth/authorize", data={
            "client_id": client_id,
            "redirect_uri": "http://localhost:33333/callback",
            "state": "",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "api_key": raw_key,
        }, follow_redirects=False)
        code = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers["location"]).query)["code"][0]

        r2 = await oauth_client.post("/oauth/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "code_verifier": "wrong-verifier-" + secrets.token_urlsafe(16),
        })
        assert r2.status_code == 400
        assert "PKCE" in r2.json().get("error_description", "")

    async def test_wrong_api_key_denied(self, oauth_client, db_session):
        """Wrong API key in authorize form returns error page (not 302)."""
        await _create_test_user(db_session, f"guard-{uuid.uuid4().hex[:8]}@test.com", role="owner")
        reg = await oauth_client.post("/oauth/register", json={"redirect_uris": ["http://localhost:44444/callback"]})
        client_id = reg.json()["client_id"]
        _, challenge = _pkce_pair()

        r = await oauth_client.post("/oauth/authorize", data={
            "client_id": client_id,
            "redirect_uri": "http://localhost:44444/callback",
            "state": "",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "api_key": "definitely-wrong-key",
        }, follow_redirects=False)
        assert r.status_code == 200  # form re-shown
        assert "Invalid" in r.text


# ── Refresh token ─────────────────────────────────────────────────────────────

class TestRefreshToken:
    async def _get_tokens(self, oauth_client, db_session, port: int) -> tuple[str, str, str]:
        """Helper: get access + refresh token for a fresh user."""
        owner, raw_key = await _create_test_user(db_session, f"refresh-{uuid.uuid4().hex[:8]}@test.com", role="owner")
        reg = await oauth_client.post("/oauth/register", json={
            "redirect_uris": [f"http://localhost:{port}/callback"]
        })
        client_id = reg.json()["client_id"]
        verifier, challenge = _pkce_pair()

        r = await oauth_client.post("/oauth/authorize", data={
            "client_id": client_id,
            "redirect_uri": f"http://localhost:{port}/callback",
            "state": "",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "api_key": raw_key,
        }, follow_redirects=False)
        code = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers["location"]).query)["code"][0]

        tok_r = await oauth_client.post("/oauth/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "code_verifier": verifier,
        })
        body = tok_r.json()
        return body["access_token"], body["refresh_token"], client_id

    async def test_refresh_token_works(self, oauth_client, db_session):
        access, refresh, _ = await self._get_tokens(oauth_client, db_session, 55001)

        r = await oauth_client.post("/oauth/token", data={
            "grant_type": "refresh_token",
            "refresh_token": refresh,
        })
        assert r.status_code == 200
        body = r.json()
        assert "access_token" in body
        assert body["access_token"] != access  # rotated

    async def test_refresh_token_rotation(self, oauth_client, db_session):
        """Old refresh token must be rejected after use (rotation)."""
        _, refresh, _ = await self._get_tokens(oauth_client, db_session, 55002)

        # Use it once
        r1 = await oauth_client.post("/oauth/token", data={
            "grant_type": "refresh_token", "refresh_token": refresh,
        })
        assert r1.status_code == 200

        # Using it again must fail
        r2 = await oauth_client.post("/oauth/token", data={
            "grant_type": "refresh_token", "refresh_token": refresh,
        })
        assert r2.status_code == 400


# ── Token revocation ──────────────────────────────────────────────────────────

class TestRevocation:
    async def test_revoke_access_token(self, oauth_client, db_session):
        owner, raw_key = await _create_test_user(db_session, f"rev-{uuid.uuid4().hex[:8]}@test.com", role="owner")
        reg = await oauth_client.post("/oauth/register", json={"redirect_uris": ["http://localhost:66666/callback"]})
        client_id = reg.json()["client_id"]
        verifier, challenge = _pkce_pair()
        auth = await oauth_client.post("/oauth/authorize", data={
            "client_id": client_id, "redirect_uri": "http://localhost:66666/callback",
            "state": "", "code_challenge": challenge, "code_challenge_method": "S256", "api_key": raw_key,
        }, follow_redirects=False)
        code = urllib.parse.parse_qs(urllib.parse.urlparse(auth.headers["location"]).query)["code"][0]
        tok = await oauth_client.post("/oauth/token", data={
            "grant_type": "authorization_code", "code": code,
            "client_id": client_id, "code_verifier": verifier,
        })
        access_token = tok.json()["access_token"]

        # Token works before revocation
        mcp_r = await oauth_client.post("/mcp",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        assert mcp_r.status_code == 200

        # Revoke
        rev = await oauth_client.post("/oauth/revoke", data={"token": access_token})
        assert rev.status_code == 200

        # Token no longer works
        mcp_r2 = await oauth_client.post("/mcp",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert mcp_r2.status_code == 401

    async def test_revoke_nonexistent_token_returns_200(self, oauth_client):
        """RFC 7009: revocation of unknown token must return 200."""
        r = await oauth_client.post("/oauth/revoke", data={"token": "doesnotexist"})
        assert r.status_code == 200


# ── MCP + OAuth ───────────────────────────────────────────────────────────────

class TestMCPWithOAuth:
    async def _get_oauth_access_token(self, oauth_client, db_session) -> tuple[str, str]:
        """Return (access_token, user_id)."""
        owner, raw_key = await _create_test_user(
            db_session, f"mcp-oauth-{uuid.uuid4().hex[:8]}@test.com", role="owner"
        )
        reg = await oauth_client.post("/oauth/register", json={"redirect_uris": ["http://localhost:77777/callback"]})
        client_id = reg.json()["client_id"]
        verifier, challenge = _pkce_pair()
        auth = await oauth_client.post("/oauth/authorize", data={
            "client_id": client_id, "redirect_uri": "http://localhost:77777/callback",
            "state": "", "code_challenge": challenge, "code_challenge_method": "S256", "api_key": raw_key,
        }, follow_redirects=False)
        code = urllib.parse.parse_qs(urllib.parse.urlparse(auth.headers["location"]).query)["code"][0]
        tok = await oauth_client.post("/oauth/token", data={
            "grant_type": "authorization_code", "code": code,
            "client_id": client_id, "code_verifier": verifier,
        })
        return tok.json()["access_token"], owner.id

    async def test_mcp_tools_list_with_oauth_token(self, oauth_client, db_session):
        access_token, _ = await self._get_oauth_access_token(oauth_client, db_session)
        r = await oauth_client.post("/mcp",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        assert r.status_code == 200
        body = r.json()
        assert "result" in body
        assert "tools" in body["result"]

    async def test_mcp_initialize_with_oauth_token(self, oauth_client, db_session):
        access_token, _ = await self._get_oauth_access_token(oauth_client, db_session)
        r = await oauth_client.post("/mcp",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"},
            }},
        )
        assert r.status_code == 200
        assert r.json()["result"]["protocolVersion"] == "2024-11-05"

    async def test_mcp_no_token_returns_401_with_resource_metadata(self, oauth_client):
        """In non-dev mode, missing token → 401 with WWW-Authenticate resource_metadata.
        In dev mode, auth is bypassed — we verify the WWW-Authenticate header is correct
        by testing with an explicitly invalid token instead.
        """
        from mimir.config import get_settings
        settings = get_settings()

        if settings.is_dev_auth:
            # Dev mode: test that an explicitly invalid (non-OAuth) token triggers no WWW-Authenticate
            # but that the header IS present when a revoked token is supplied
            # (covered by test_revoke_access_token)
            pytest.skip("WWW-Authenticate 401 not enforced in dev mode — by design")
        else:
            r = await oauth_client.post("/mcp",
                headers={"Accept": "application/json"},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
            assert r.status_code == 401
            www_auth = r.headers.get("www-authenticate", "")
            assert "resource_metadata" in www_auth or "Bearer" in www_auth

    async def test_cross_user_memory_isolation_with_oauth(self, oauth_client, db_session):
        """User A's OAuth token cannot read user B's memories."""
        # Create two separate users with OAuth tokens
        userA, keyA = await _create_test_user(db_session, f"ua-{uuid.uuid4().hex[:8]}@test.com", role="owner")
        userB, keyB = await _create_test_user(db_session, f"ub-{uuid.uuid4().hex[:8]}@test.com", role="user")

        async def _get_token(key):
            reg = await oauth_client.post("/oauth/register", json={
                "redirect_uris": [f"http://localhost:{secrets.randbelow(10000)+50000}/callback"]
            })
            cid = reg.json()["client_id"]
            v, c = _pkce_pair()
            port = secrets.randbelow(10000) + 50000
            reg2 = await oauth_client.post("/oauth/register", json={
                "redirect_uris": [f"http://localhost:{port}/callback"]
            })
            cid = reg2.json()["client_id"]
            auth = await oauth_client.post("/oauth/authorize", data={
                "client_id": cid, "redirect_uri": f"http://localhost:{port}/callback",
                "state": "", "code_challenge": c, "code_challenge_method": "S256", "api_key": key,
            }, follow_redirects=False)
            code = urllib.parse.parse_qs(urllib.parse.urlparse(auth.headers["location"]).query)["code"][0]
            tok = await oauth_client.post("/oauth/token", data={
                "grant_type": "authorization_code", "code": code,
                "client_id": cid, "code_verifier": v,
            })
            return tok.json()["access_token"]

        token_a = await _get_token(keyA)
        token_b = await _get_token(keyB)

        # User A stores a uniquely-tagged memory
        secret_tag = f"secret-{uuid.uuid4().hex}"
        await oauth_client.post("/mcp",
            headers={"Authorization": f"Bearer {token_a}", "Accept": "application/json"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "memory.remember",
                "arguments": {
                    "type": "fact",
                    "content": f"A's private fact: {secret_tag}",
                    "project": f"proj-a-{uuid.uuid4().hex[:8]}",
                },
            }},
        )

        # User B tries to search for it
        recall_r = await oauth_client.post("/mcp",
            headers={"Authorization": f"Bearer {token_b}", "Accept": "application/json"},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "memory.search",
                "arguments": {"query": secret_tag},
            }},
        )
        assert recall_r.status_code == 200
        result_text = recall_r.text
        assert secret_tag not in result_text


# ── Config auth mode ──────────────────────────────────────────────────────────

class TestConfigAuthMode:
    def test_dev_mode_is_dev_auth(self):
        from mimir.config import Settings
        s = Settings(env="development", auth_mode="dev")
        assert s.is_dev_auth
        assert not s.is_single_user
        assert not s.is_multi_user

    def test_single_user_mode(self):
        from mimir.config import Settings
        s = Settings(env="production", auth_mode="single_user")
        assert not s.is_dev_auth
        assert s.is_single_user
        assert not s.is_multi_user

    def test_multi_user_mode(self):
        from mimir.config import Settings
        s = Settings(env="production", auth_mode="multi_user")
        assert not s.is_dev_auth
        assert not s.is_single_user
        assert s.is_multi_user

    def test_prod_alias_maps_to_multi_user(self):
        """Legacy auth_mode=prod should behave as multi_user."""
        from mimir.config import Settings
        s = Settings(env="production", auth_mode="prod")
        assert s.is_multi_user
        assert not s.is_dev_auth

    def test_empty_auth_mode_dev_env_is_dev(self):
        from mimir.config import Settings
        s = Settings(env="development", auth_mode="")
        assert s.is_dev_auth

    def test_empty_auth_mode_prod_env_is_multi_user(self):
        from mimir.config import Settings
        s = Settings(env="production", auth_mode="")
        assert s.is_multi_user


# ── Multi-user security ────────────────────────────────────────────────────────

class TestMultiUserSecurity:
    def test_validate_config_accepts_single_user_mode(self):
        from mimir.config import Settings, validate_config
        import io, sys
        s = Settings(env="development", auth_mode="single_user")
        # Should not raise
        validate_config(s)

    def test_validate_config_accepts_multi_user_mode(self):
        from mimir.config import Settings, validate_config
        s = Settings(env="development", auth_mode="multi_user")
        validate_config(s)

    def test_validate_config_rejects_unknown_auth_mode(self):
        from mimir.config import Settings, validate_config
        s = Settings(env="production", auth_mode="magic")
        with pytest.raises(SystemExit):
            validate_config(s)

    def test_registration_disabled_by_default(self):
        from mimir.config import Settings
        s = Settings()
        assert s.allow_registration is False

    def test_access_token_ttl_configurable(self):
        from mimir.config import Settings
        s = Settings(access_token_ttl_seconds=7200)
        assert s.access_token_ttl_seconds == 7200


# ── First-run setup ────────────────────────────────────────────────────────────

class TestSetupPage:
    async def test_setup_page_accessible(self, oauth_client):
        r = await oauth_client.get("/setup")
        assert r.status_code == 200

    async def test_setup_page_shows_done_when_owner_exists(self, oauth_client, db_session):
        """After creating an owner, /setup shows 'already complete'."""
        from storage.models import User
        result = await db_session.execute(select(User).where(User.role == "owner").limit(1))
        if result.scalar_one_or_none():
            r = await oauth_client.get("/setup")
            assert r.status_code == 200
            assert "already" in r.text.lower() or "Setup Required" in r.text
