"""P21 — One-Command Onboarding + Repo Connection UX Hardening tests.

Covers:
- /api/system/doctor endpoint (unauthenticated)
- /api/projects and /api/projects/{slug} (authenticated)
- MCP connection tracking (_mcp_tracker)
- Default single_user auth mode
"""
from __future__ import annotations

import hashlib
import secrets
import uuid

import pytest

# In dev/test mode (MIMIR_ENV=development), get_current_user always returns the dev user.
# Tests that write memories and then query via the API must use DEV_USER_ID so the
# project route's user_id filter sees the same value as current_user.id.
from api.deps import DEV_USER_ID


async def _create_user_and_key(email: str, role: str = "owner") -> tuple[str, str]:
    from storage.models import APIKey, User
    from storage.database import get_session_factory

    user_id = uuid.uuid4().hex
    raw_key = secrets.token_urlsafe(24)
    factory = get_session_factory()
    async with factory() as session:
        session.add(User(
            id=user_id,
            email=email,
            display_name="P21 Tester",
            role=role,
        ))
        session.add(APIKey(
            id=uuid.uuid4().hex,
            user_id=user_id,
            key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
            name="p21-test",
        ))
        await session.commit()
    return user_id, raw_key


# ── Doctor endpoint ────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_doctor_accessible_without_auth(client):
    """Doctor is intentionally unauthenticated — returns setup state."""
    response = await client.get("/api/system/doctor")
    assert response.status_code == 200
    body = response.json()
    assert "status" in body
    assert "auth_mode" in body
    assert "mcp_url" in body
    assert "warnings" in body
    assert "fix_suggestions" in body
    assert "checked_at" in body


@pytest.mark.anyio
async def test_doctor_contains_setup_links(client):
    response = await client.get("/api/system/doctor")
    assert response.status_code == 200
    body = response.json()
    links = body["setup_links"]
    assert "dashboard" in links
    assert "connection_settings" in links
    assert "first_run_setup" in links
    assert "projects" in links


@pytest.mark.anyio
async def test_doctor_owner_exists_field_present(client):
    """Doctor always reports owner_exists as a boolean."""
    response = await client.get("/api/system/doctor")
    body = response.json()
    assert "owner_exists" in body
    assert isinstance(body["owner_exists"], bool)


@pytest.mark.anyio
async def test_doctor_owner_exists_true_after_owner_created(client):
    await _create_user_and_key("doctor_owner@test.invalid")
    response = await client.get("/api/system/doctor")
    body = response.json()
    assert body["owner_exists"] is True


@pytest.mark.anyio
async def test_doctor_bootstrapped_projects_list(client):
    """After writing a bootstrap memory, the project appears in doctor output."""
    from storage.models import Memory
    from storage.database import get_session_factory

    user_id = uuid.uuid4().hex
    factory = get_session_factory()
    async with factory() as session:
        session.add(Memory(
            id=uuid.uuid4().hex,
            user_id=user_id,
            layer="semantic",
            content="test project profile",
            project="doctor_test_proj",
            source_type="project_bootstrap",
            memory_state="active",
            importance=0.9,
        ))
        await session.commit()

    response = await client.get("/api/system/doctor")
    body = response.json()
    assert "doctor_test_proj" in body["bootstrapped_projects"]


@pytest.mark.anyio
async def test_doctor_warnings_are_well_formed(client):
    """All doctor warnings have required fields."""
    response = await client.get("/api/system/doctor")
    body = response.json()
    for w in body["warnings"]:
        assert "code" in w
        assert "severity" in w
        assert "message" in w


@pytest.mark.anyio
async def test_doctor_includes_mcp_status_field(client):
    response = await client.get("/api/system/doctor")
    body = response.json()
    assert "mcp_last_connection" in body


@pytest.mark.anyio
async def test_doctor_database_mode_field(client):
    response = await client.get("/api/system/doctor")
    body = response.json()
    assert body["database_mode"] in ("sqlite", "postgres")


# ── Projects API ───────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_projects_list_returns_valid_shape(client):
    """Project list endpoint returns the expected JSON structure."""
    response = await client.get("/api/projects")
    assert response.status_code == 200
    body = response.json()
    assert "projects" in body
    assert isinstance(body["projects"], list)
    assert "count" in body
    assert body["count"] == len(body["projects"])


@pytest.mark.anyio
async def test_projects_list_shows_bootstrapped_project(client):
    from storage.models import Memory
    from storage.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        session.add(Memory(
            id=uuid.uuid4().hex,
            user_id=DEV_USER_ID,  # must match dev auth user
            layer="semantic",
            content="mimir project profile",
            project="p21_proj",
            source_type="project_bootstrap",
            memory_state="active",
            importance=0.9,
            meta={"capsule_type": "project_profile"},
        ))
        await session.commit()

    response = await client.get("/api/projects")
    assert response.status_code == 200
    body = response.json()
    # At least one project should be present (may have others from prior tests)
    slugs = [p["project"] for p in body["projects"]]
    assert "p21_proj" in slugs
    proj = next(p for p in body["projects"] if p["project"] == "p21_proj")
    assert proj["memory_count"] >= 1
    assert "project_profile" in proj["bootstrap"]["present_capsule_types"]


@pytest.mark.anyio
async def test_projects_isolation_between_users(client):
    """Memories written with a different user_id must not appear in the dev user's project list."""
    from storage.models import Memory
    from storage.database import get_session_factory

    other_user_id = uuid.uuid4().hex  # not dev, not in the current request's user
    factory = get_session_factory()
    async with factory() as session:
        session.add(Memory(
            id=uuid.uuid4().hex,
            user_id=other_user_id,
            layer="semantic",
            content="other user memory",
            project="p21_isol_other_only",
            source_type="project_bootstrap",
            memory_state="active",
            importance=0.8,
        ))
        await session.commit()

    # dev user requests their projects — must not see other_user's project
    response = await client.get("/api/projects")
    body = response.json()
    project_slugs = [p["project"] for p in body["projects"]]
    assert "p21_isol_other_only" not in project_slugs


@pytest.mark.anyio
async def test_project_slug_detail_partial_bootstrap(client):
    from storage.models import Memory
    from storage.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        for capsule_type in ["project_profile", "architecture_summary"]:
            session.add(Memory(
                id=uuid.uuid4().hex,
                user_id=DEV_USER_ID,
                layer="semantic",
                content=f"capsule {capsule_type}",
                project="p21_detail_proj",
                source_type="project_bootstrap",
                memory_state="active",
                importance=0.9,
                meta={"capsule_type": capsule_type},
            ))
        await session.commit()

    response = await client.get("/api/projects/p21_detail_proj")
    assert response.status_code == 200
    body = response.json()
    assert body["project"] == "p21_detail_proj"
    assert body["bootstrap"]["health"] == "partial"
    assert "project_profile" in body["bootstrap"]["present_capsule_types"]
    assert len(body["bootstrap"]["missing_capsule_types"]) > 0


@pytest.mark.anyio
async def test_project_healthy_when_all_capsules_present(client):
    from storage.models import Memory
    from storage.database import get_session_factory
    from api.routes.projects import _BOOTSTRAP_CAPSULE_TYPES

    factory = get_session_factory()
    async with factory() as session:
        for capsule_type in _BOOTSTRAP_CAPSULE_TYPES:
            session.add(Memory(
                id=uuid.uuid4().hex,
                user_id=DEV_USER_ID,
                layer="semantic",
                content=f"capsule {capsule_type}",
                project="p21_healthy_proj",
                source_type="project_bootstrap",
                memory_state="active",
                importance=0.9,
                meta={"capsule_type": capsule_type},
            ))
        await session.commit()

    response = await client.get("/api/projects/p21_healthy_proj")
    assert response.status_code == 200
    body = response.json()
    assert body["bootstrap"]["health"] == "healthy"
    assert body["bootstrap"]["missing_capsule_types"] == []


@pytest.mark.anyio
async def test_projects_returns_valid_shape(client):
    response = await client.get("/api/projects")
    # In dev mode always 200; in prod mode 401 without key
    assert response.status_code in (200, 401)
    if response.status_code == 200:
        body = response.json()
        assert "projects" in body
        assert "count" in body


# ── MCP connection tracker ─────────────────────────────────────────────────────

def test_mcp_tracker_records_connection():
    from api.routes._mcp_tracker import record_mcp_connection, get_mcp_status, _last_connection
    _last_connection.clear()

    record_mcp_connection(user_id="u1", auth_method="api_key", client_name="cursor")
    status = get_mcp_status()
    assert status is not None
    assert status["auth_method"] == "api_key"
    assert status["user_id"] == "u1"
    assert status["client_name"] == "cursor"
    assert "connected_at" in status


def test_mcp_tracker_returns_none_when_empty():
    from api.routes._mcp_tracker import get_mcp_status, _last_connection
    _last_connection.clear()
    assert get_mcp_status() is None


def test_mcp_tracker_overwrites_on_second_connection():
    from api.routes._mcp_tracker import record_mcp_connection, get_mcp_status, _last_connection
    _last_connection.clear()

    record_mcp_connection(user_id="u1", auth_method="api_key")
    record_mcp_connection(user_id="u2", auth_method="oauth", client_name="browser")
    status = get_mcp_status()
    assert status["user_id"] == "u2"
    assert status["auth_method"] == "oauth"


# ── Single-user auth mode ──────────────────────────────────────────────────────

def test_single_user_effective_mode():
    """With MIMIR_AUTH_MODE=single_user the effective mode is single_user."""
    import os
    from unittest.mock import patch
    with patch.dict(os.environ, {"MIMIR_AUTH_MODE": "single_user"}, clear=False):
        from mimir import config as cfg
        s = cfg.Settings(_env_file=None)  # type: ignore[call-arg]
        assert s._effective_auth_mode == "single_user"
        assert s.is_single_user is True


def test_multi_user_effective_mode():
    """With MIMIR_AUTH_MODE=multi_user the effective mode is multi_user."""
    import os
    from unittest.mock import patch
    with patch.dict(os.environ, {"MIMIR_AUTH_MODE": "multi_user"}, clear=False):
        from mimir import config as cfg
        s = cfg.Settings(_env_file=None)  # type: ignore[call-arg]
        assert s._effective_auth_mode == "multi_user"
        assert s.is_multi_user is True


def test_dev_mode_is_default_in_development_env():
    """With no auth_mode set and MIMIR_ENV=development, effective mode is dev."""
    import os
    from unittest.mock import patch
    with patch.dict(os.environ, {"MIMIR_AUTH_MODE": "", "MIMIR_ENV": "development"}, clear=False):
        from mimir import config as cfg
        s = cfg.Settings(_env_file=None)  # type: ignore[call-arg]
        assert s._effective_auth_mode == "dev"
        assert s.is_dev_auth is True
