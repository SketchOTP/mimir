from __future__ import annotations

import hashlib
import secrets
import uuid

import pytest

from mimir.setup_profile import load_setup_profile, save_setup_profile


async def _create_user_and_key(session, email: str, role: str = "owner") -> tuple[str, str]:
    from storage.models import APIKey, User

    user_id = uuid.uuid4().hex
    raw_key = secrets.token_urlsafe(24)
    session.add(User(
        id=user_id,
        email=email,
        display_name="Connection Tester",
        role=role,
    ))
    session.add(APIKey(
        id=uuid.uuid4().hex,
        user_id=user_id,
        key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
        name="primary",
    ))
    await session.commit()
    return user_id, raw_key


@pytest.mark.anyio
async def test_connection_settings_page_loads(client):
    response = await client.get("/settings/connection")
    assert response.status_code == 200
    assert "Connection Setup" in response.text
    assert "Create New API Key" in response.text


@pytest.mark.anyio
async def test_connection_profile_can_be_read(app, client):
    from storage.database import get_session_factory

    save_setup_profile({
        "use_case": "lan_browser",
        "preferred_auth": "oauth",
        "public_url": "http://192.168.1.55:8787",
        "ssh_host": "",
        "remote_mimir_path": "",
        "cursor_mcp_path": "~/.cursor/mcp.json",
        "remote_python_path": "",
        "notes": "LAN box",
    })
    factory = get_session_factory()
    async with factory() as session:
        _, raw_key = await _create_user_and_key(session, f"read-{uuid.uuid4().hex[:8]}@test.com")

    response = await client.get("/api/connection/settings", headers={"X-API-Key": raw_key})
    assert response.status_code == 200
    body = response.json()
    assert body["profile"]["use_case"] == "lan_browser"
    assert body["profile"]["public_url"] == "http://192.168.1.55:8787"
    assert body["generated_configs"]["cursor_local"]["label"] == "Cursor Local"


@pytest.mark.anyio
async def test_connection_profile_can_be_updated(client):
    from storage.database import get_session_factory
    from mimir.config import get_settings

    settings = get_settings()
    original_mode = settings.auth_mode
    object.__setattr__(settings, "auth_mode", "single_user")
    try:
        factory = get_session_factory()
        async with factory() as session:
            _, raw_key = await _create_user_and_key(session, f"update-{uuid.uuid4().hex[:8]}@test.com")

        response = await client.put(
            "/api/connection/settings",
            headers={"X-API-Key": raw_key},
            json={
                "use_case": "ssh_remote",
                "preferred_auth": "api_key",
                "public_url": "http://192.168.1.80:8787",
                "ssh_host": "atlas",
                "remote_mimir_path": "/srv/mimir",
                "cursor_mcp_path": "~/.cursor/mcp.json",
                "remote_python_path": "/srv/mimir/.venv/bin/python",
                "notes": "remote box",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["profile"]["use_case"] == "ssh_remote"
        assert body["profile"]["ssh_host"] == "atlas"
        saved = load_setup_profile()
        assert saved["remote_mimir_path"] == "/srv/mimir"
        assert saved["preferred_auth"] == "api_key"
    finally:
        object.__setattr__(settings, "auth_mode", original_mode)


@pytest.mark.anyio
async def test_generated_config_matches_selected_connection_type(client):
    from storage.database import get_session_factory

    save_setup_profile({
        "use_case": "ssh_remote",
        "preferred_auth": "api_key",
        "public_url": "https://mimir.example.com",
        "ssh_host": "atlas",
        "remote_mimir_path": "/srv/mimir",
        "cursor_mcp_path": "~/.cursor/mcp.json",
        "remote_python_path": "/srv/mimir/.venv/bin/python",
        "notes": "",
    })
    factory = get_session_factory()
    async with factory() as session:
        _, raw_key = await _create_user_and_key(session, f"cfg-{uuid.uuid4().hex[:8]}@test.com")

    response = await client.get("/api/connection/settings", headers={"X-API-Key": raw_key})
    body = response.json()
    ssh_json = body["generated_configs"]["cursor_ssh"]["json"]
    local_json = body["generated_configs"]["cursor_local"]["json"]
    assert '"Authorization": "Bearer YOUR_API_KEY"' in ssh_json
    assert '"url": "https://mimir.example.com/mcp"' in local_json
    assert '"Authorization": "Bearer YOUR_API_KEY"' not in local_json


@pytest.mark.anyio
async def test_api_key_generated_once_and_not_listed_again(client):
    from storage.database import get_session_factory
    from mimir.config import get_settings

    settings = get_settings()
    original_mode = settings.auth_mode
    object.__setattr__(settings, "auth_mode", "single_user")
    try:
        factory = get_session_factory()
        async with factory() as session:
            _, raw_key = await _create_user_and_key(session, f"key-{uuid.uuid4().hex[:8]}@test.com")

        created = await client.post("/api/auth/keys?name=secondary", headers={"X-API-Key": raw_key})
        assert created.status_code == 200
        created_body = created.json()
        assert created_body["api_key"]

        listed = await client.get("/api/auth/keys", headers={"X-API-Key": raw_key})
        assert listed.status_code == 200
        listed_body = listed.json()
        serialized = str(listed_body)
        assert created_body["api_key"] not in serialized
        assert all("key_hash" not in key for key in listed_body["keys"])
    finally:
        object.__setattr__(settings, "auth_mode", original_mode)


@pytest.mark.anyio
async def test_invalid_public_url_warning_works(client):
    from storage.database import get_session_factory

    save_setup_profile({
        "use_case": "ssh_remote",
        "preferred_auth": "api_key",
        "public_url": "http://127.0.0.1:8787",
        "ssh_host": "atlas",
        "remote_mimir_path": "/srv/mimir",
        "cursor_mcp_path": "",
        "remote_python_path": "",
        "notes": "",
    })
    factory = get_session_factory()
    async with factory() as session:
        _, raw_key = await _create_user_and_key(session, f"warn-{uuid.uuid4().hex[:8]}@test.com")

    response = await client.get("/api/connection/settings", headers={"X-API-Key": raw_key})
    codes = {warning["code"] for warning in response.json()["warnings"]}
    assert "public_url_localhost_remote" in codes


@pytest.mark.anyio
async def test_remote_oauth_warning_works(client):
    from storage.database import get_session_factory

    save_setup_profile({
        "use_case": "headless",
        "preferred_auth": "oauth",
        "public_url": "http://192.168.1.70:8787",
        "ssh_host": "",
        "remote_mimir_path": "",
        "cursor_mcp_path": "",
        "remote_python_path": "",
        "notes": "",
    })
    factory = get_session_factory()
    async with factory() as session:
        _, raw_key = await _create_user_and_key(session, f"oauth-{uuid.uuid4().hex[:8]}@test.com")

    response = await client.get("/api/connection/settings", headers={"X-API-Key": raw_key})
    codes = {warning["code"] for warning in response.json()["warnings"]}
    assert "remote_oauth_without_device_code" in codes
    assert "api_key_recommended_remote" in codes
