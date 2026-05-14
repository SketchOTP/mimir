"""P19 acceptance tests — MCP Streamable HTTP endpoint.

Tests:
  - POST /mcp requires auth (prod mode)
  - POST /mcp never returns 405
  - GET /mcp does not return 404 (returns SSE stream)
  - initialize handshake returns SSE-format response
  - tools/list returns all required tools with valid names (^[A-Za-z0-9_]+$)
  - memory_remember works
  - memory_recall works
  - invalid API key returns 401
  - notifications/initialized returns 202
  - cross-user isolation preserved
  - dotted legacy names are accepted as aliases (not advertised)
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

# ── helpers ───────────────────────────────────────────────────────────────────

_BEARER = {"Authorization": "Bearer local-dev-key", "Accept": "application/json, text/event-stream"}


def _rpc(method: str, params: dict | None = None, req_id: int | None = 1) -> dict:
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if req_id is not None:
        msg["id"] = req_id
    if params is not None:
        msg["params"] = params
    return msg


async def _post(client: AsyncClient, method: str, params: dict | None = None) -> dict:
    r = await client.post("/mcp", json=_rpc(method, params), headers=_BEARER)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    return r


def _decode_sse(text: str) -> dict:
    """Extract the JSON-RPC object from an SSE event: message\ndata: <json>."""
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    raise AssertionError(f"No data: line in SSE response: {text!r}")


def _tool_payload(resp) -> dict:
    data = _decode_sse(resp.text)
    assert "result" in data, data
    return json.loads(data["result"]["content"][0]["text"])


def _mock_prod_settings(api_key: str = "test-secret-key"):
    s = MagicMock()
    s.is_dev_auth = False
    s.api_key = api_key
    return s


# ── P19-1: POST /mcp requires auth ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_mcp_requires_auth_in_prod_mode(client):
    """POST /mcp without auth returns 401 in prod mode — never 405."""
    with patch("api.routes.mcp_http.get_settings", return_value=_mock_prod_settings()):
        r = await client.post("/mcp", json=_rpc("tools/list"))
    assert r.status_code == 401
    assert r.status_code != 405, "401 expected, not 405 (405 means route missing)"


# ── P19-2: POST /mcp never returns 405 ───────────────────────────────────────

@pytest.mark.asyncio
async def test_mcp_post_never_405(client):
    """Sanity: POST /mcp always finds a route — never 405 Method Not Allowed."""
    r = await client.post("/mcp", json=_rpc("tools/list"), headers=_BEARER)
    assert r.status_code != 405, f"Got 405 — route not registered: {r.text}"
    assert r.status_code == 200


# ── P19-3: GET /mcp does not return 404 ──────────────────────────────────────

@pytest.mark.asyncio
async def test_mcp_get_not_404(client):
    """GET /mcp must not return 404 — opens SSE stream per spec §6.4.1."""
    import asyncio

    try:
        async with asyncio.timeout(0.5):
            async with client.stream("GET", "/mcp", headers=_BEARER) as r:
                assert r.status_code == 200, f"GET /mcp returned {r.status_code}"
                assert "text/event-stream" in r.headers.get("content-type", "")
                # Read first chunk to confirm the stream opened
                async for chunk in r.aiter_bytes():
                    assert chunk  # at least ": connected\n\n"
                    break
    except asyncio.TimeoutError:
        pass  # stream staying open is correct


# ── P19-4: initialize returns SSE format ─────────────────────────────────────

@pytest.mark.asyncio
async def test_mcp_initialize_sse_format(client):
    """initialize with Accept: text/event-stream returns SSE-wrapped JSON-RPC."""
    r = await _post(client, "initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "cursor-test", "version": "1.0"},
    })
    ct = r.headers.get("content-type", "")
    assert "text/event-stream" in ct, f"Expected text/event-stream, got {ct!r}"

    data = _decode_sse(r.text)
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 1
    result = data["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"] == "mimir"


# ── P19-5: tools/list returns required tools with valid names ─────────────────

import re
_VALID_NAME = re.compile(r'^[A-Za-z0-9_]+$')

@pytest.mark.asyncio
async def test_mcp_lists_tools(client):
    """tools/list returns all required tools; all names match ^[A-Za-z0-9_]+$."""
    r = await _post(client, "tools/list")
    data = _decode_sse(r.text)
    assert "result" in data
    tools = data["result"]["tools"]
    names = {t["name"] for t in tools}
    required = {
        "memory_remember", "memory_recall", "memory_search",
        "memory_record_outcome", "skill_list", "approval_request",
        "approval_status", "reflection_log", "improvement_propose",
        "project_bootstrap",
    }
    assert required.issubset(names), f"Missing: {required - names}"
    invalid = [n for n in names if not _VALID_NAME.match(n)]
    assert not invalid, f"Invalid tool names (dots not allowed): {invalid}"


# ── P19-6: memory_remember works ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mcp_memory_remember(client):
    """tools/call memory_remember stores a memory and returns {ok, stored}."""
    r = await _post(client, "tools/call", {
        "name": "memory_remember",
        "arguments": {
            "type": "fact",
            "content": "P19.1 MCP HTTP test: Cursor prefers dark mode",
            "project": "p19_test",
        },
    })
    data = _decode_sse(r.text)
    assert "result" in data, data
    payload = json.loads(data["result"]["content"][0]["text"])
    assert payload.get("ok") is True
    assert isinstance(payload.get("stored"), list)


# ── P19-7: memory_recall works ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mcp_memory_recall(client):
    """tools/call memory_recall returns hits for a stored memory."""
    await _post(client, "tools/call", {
        "name": "memory_remember",
        "arguments": {
            "type": "fact",
            "content": "P19.1 recall test: agent uses vim keybindings",
            "project": "p19_recall",
        },
    })

    r = await _post(client, "tools/call", {
        "name": "memory_recall",
        "arguments": {"query": "vim keybindings", "project": "p19_recall"},
    })
    data = _decode_sse(r.text)
    assert "result" in data, data
    payload = json.loads(data["result"]["content"][0]["text"])
    assert "hits" in payload or "memories" in payload or "context" in payload


# ── P19-8: invalid API key returns 401 ───────────────────────────────────────

@pytest.mark.asyncio
async def test_mcp_invalid_key_returns_401(client):
    """Wrong Bearer token returns 401 in prod mode, not 405."""
    with patch("api.routes.mcp_http.get_settings", return_value=_mock_prod_settings("test-key")):
        r = await client.post(
            "/mcp",
            json=_rpc("tools/list"),
            headers={"Authorization": "Bearer wrong-key", "Accept": "application/json, text/event-stream"},
        )
    assert r.status_code == 401
    assert r.status_code != 405


# ── P19-9: notifications/initialized returns 202 ─────────────────────────────

@pytest.mark.asyncio
async def test_mcp_notification_returns_202(client):
    """notifications/initialized (no id) returns HTTP 202 per spec §6.4.1."""
    msg = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
    r = await client.post("/mcp", json=msg, headers=_BEARER)
    assert r.status_code == 202, f"Expected 202, got {r.status_code}"
    assert not r.content  # no body


# ── P19-10: cross-user isolation ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mcp_cross_user_isolation(client, app):
    """User B cannot recall memories stored by user A via the MCP endpoint."""
    from api.deps import get_current_user, UserContext

    async def _as_user_a():
        return UserContext(id="mcp19_user_a", email="a@test.com", display_name="User A", is_dev=False)

    async def _as_user_b():
        return UserContext(id="mcp19_user_b", email="b@test.com", display_name="User B", is_dev=False)

    app.dependency_overrides[get_current_user] = _as_user_a
    try:
        r = await client.post("/api/events", json={
            "type": "fact",
            "content": "P19.1 isolation: user A's secret p19_isolation_xyz",
            "project": "p19_isolation2",
        })
        assert r.status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    app.dependency_overrides[get_current_user] = _as_user_b
    try:
        r = await client.post("/api/events/recall", json={
            "query": "p19_isolation_xyz secret",
            "project": "p19_isolation2",
        })
        assert r.status_code == 200
        hits = r.json().get("hits", [])
        assert not any("user A's secret" in h.get("content", "") for h in hits)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ── project.bootstrap tests ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mcp_bootstrap_listed_in_tools(client):
    """tools/list includes project_bootstrap with a valid name."""
    r = await _post(client, "tools/list")
    data = _decode_sse(r.text)
    names = {t["name"] for t in data["result"]["tools"]}
    assert "project_bootstrap" in names
    assert "project.bootstrap" not in names, "Dotted name must not be advertised"


@pytest.mark.asyncio
async def test_mcp_bootstrap_requires_project(client):
    """project_bootstrap returns an error when project is missing."""
    r = await _post(client, "tools/call", {
        "name": "project_bootstrap",
        "arguments": {"profile": "some content"},
    })
    data = _decode_sse(r.text)
    assert "error" in data, data


@pytest.mark.asyncio
async def test_mcp_bootstrap_writes_memories(client):
    """project_bootstrap writes all 7 capsules and returns per-type IDs."""
    project = "bootstrap_test_writes"
    r = await _post(client, "tools/call", {
        "name": "project_bootstrap",
        "arguments": {
            "project": project,
            "repo_path": "/test/repo",
            "profile": "Test project: a demo API server. Stack: Python, FastAPI.",
            "architecture": "Monorepo with api/, web/, worker/, and tests/.",
            "status": "Active. 42 tests passing. No blockers.",
            "constraints": "Never delete production data. Always run tests before committing.",
            "testing": "pytest tests/ -v. Run make test.",
            "knowledge": "Lesson: always pin dependency versions.",
        },
    })
    payload = _tool_payload(r)
    assert payload["ok"] is True
    assert payload["total"] == 7
    assert all("id" in m for m in payload["stored"])
    assert payload["run_id"].startswith("bootstrap_")
    assert payload["project_profile_id"]
    assert payload["architecture_summary_id"]
    assert payload["active_status_id"]
    assert payload["safety_constraint_id"]
    assert payload["testing_protocol_id"]
    assert payload["procedural_lesson_id"]
    assert payload["governance_rules_id"]


@pytest.mark.asyncio
async def test_mcp_bootstrap_idempotency_guard(client):
    """Second bootstrap call without force returns ok=False and existing_count."""
    project = "bootstrap_test_idempotent"
    args = {
        "project": project,
        "profile": "Demo project.",
        "constraints": "Never break prod.",
    }
    # First call — should succeed
    r1 = await _post(client, "tools/call", {"name": "project_bootstrap", "arguments": args})
    p1 = _tool_payload(r1)
    assert p1["ok"] is True

    # Second call without force — should be blocked
    r2 = await _post(client, "tools/call", {"name": "project_bootstrap", "arguments": args})
    p2 = _tool_payload(r2)
    assert p2["ok"] is False
    assert p2["existing_count"] > 0


@pytest.mark.asyncio
async def test_mcp_bootstrap_force_overwrites(client):
    """force=true updates/reindexes without creating extra active capsule rows."""
    from storage.database import get_session_factory
    from storage.models import Memory

    project = "bootstrap_test_force"
    args = {
        "project": project,
        "repo_path": "/test/repo",
        "profile": "First run.",
        "architecture": "First architecture.",
        "status": "First status.",
        "constraints": "First constraints.",
        "testing": "First testing protocol.",
        "knowledge": "First lesson.",
    }
    await _post(client, "tools/call", {"name": "project_bootstrap", "arguments": args})

    r = await _post(client, "tools/call", {
        "name": "project_bootstrap",
        "arguments": {
            **args,
            "profile": "Second run profile.",
            "architecture": "Second architecture.",
            "status": "Second status.",
            "constraints": "Second constraints.",
            "testing": "Second testing protocol.",
            "knowledge": "Second lesson.",
            "force": True,
        },
    })
    payload = _tool_payload(r)
    assert payload["ok"] is True
    assert payload["total"] == 7

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(Memory).where(Memory.project == project, Memory.deleted_at.is_(None))
        )
        active = [
            m for m in result.scalars()
            if isinstance(m.meta, dict) and m.meta.get("bootstrap")
        ]
    capsule_types = [m.meta.get("capsule_type") for m in active if isinstance(m.meta, dict)]
    assert len(active) == 7
    assert len(set(capsule_types)) == 7


@pytest.mark.asyncio
async def test_mcp_bootstrap_skips_empty_sections(client):
    """Sections not provided are listed in skipped, not stored."""
    r = await _post(client, "tools/call", {
        "name": "project_bootstrap",
        "arguments": {
            "project": "bootstrap_test_skip",
            "profile": "Only profile provided.",
        },
    })
    payload = _tool_payload(r)
    assert payload["ok"] is True
    types_stored = {m["type"] for m in payload["stored"]}
    assert "project_profile" in types_stored
    assert "testing_protocol" in payload["skipped"]
    assert "procedural_lesson" in payload["skipped"]


@pytest.mark.asyncio
async def test_mcp_bootstrap_metadata_and_search_recall_shape(client):
    """Bootstrap memories have normalized metadata and search/recall rich fields."""
    from storage.database import get_session_factory
    from storage.models import Memory

    project = "auto"
    repo_path = "/home/sketch/auto"
    bootstrap_args = {
        "project": project,
        "repo_path": repo_path,
        "profile": "Auto project profile for Linux robotics orchestration.",
        "architecture": "Service architecture with API, scheduler, and worker.",
        "status": "Current status: stable and actively maintained.",
        "constraints": "Safety constraints: no destructive commands without approval.",
        "testing": "Testing protocol: run pytest tests/ before shipping.",
        "knowledge": "Procedural lessons: verify migrations before deploy.",
        "force": True,
    }
    payload = _tool_payload(await _post(client, "tools/call", {
        "name": "project_bootstrap",
        "arguments": bootstrap_args,
    }))
    assert payload["ok"] is True
    assert payload["missing_capsule_types"] == []

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(Memory).where(Memory.project == project, Memory.deleted_at.is_(None))
        )
        mems = [
            m for m in result.scalars()
            if isinstance(m.meta, dict) and m.meta.get("bootstrap")
        ]

    assert len(mems) == 7
    for mem in mems:
        assert mem.project == project
        assert mem.source_type == "project_bootstrap"
        assert mem.memory_state == "active"
        assert mem.verification_status == "trusted_system_observed"
        assert (mem.trust_score or 0) >= 0.8
        assert (mem.importance or 0) >= 0.8
        assert isinstance(mem.meta, dict)
        assert mem.meta.get("bootstrap") is True
        assert mem.meta.get("bootstrap_run_id", "").startswith("bootstrap_")
        assert mem.meta.get("repo_path") == repo_path
        assert mem.meta.get("project") == project
        assert mem.meta.get("project_id") == project
        assert mem.meta.get("capsule_type")

    search_payload = _tool_payload(await _post(client, "tools/call", {
        "name": "memory_search",
        "arguments": {"project": project, "query": "project_profile"},
    }))
    assert search_payload["memories"], "Expected bootstrap search results"
    first = search_payload["memories"][0]
    for field in (
        "id", "layer", "content", "score", "importance", "created_at", "project",
        "project_id", "source_type", "memory_state", "verification_status",
        "trust_score", "capsule_type", "meta",
    ):
        assert field in first
    assert "PROJECT_PROFILE:" in first["content"] or first.get("capsule_type") == "project_profile"

    recall_payload = _tool_payload(await _post(client, "tools/call", {
        "name": "memory_recall",
        "arguments": {"project": project, "query": "what is this project"},
    }))
    assert recall_payload["hits"], "Expected bootstrap recall hits"
    assert any(h.get("capsule_type") == "project_profile" for h in recall_payload["hits"])


@pytest.mark.asyncio
async def test_mcp_bootstrap_project_isolation(client):
    """Wrong project must not return auto bootstrap memories."""
    _tool_payload(await _post(client, "tools/call", {
        "name": "project_bootstrap",
        "arguments": {
            "project": "auto",
            "repo_path": "/home/sketch/auto",
            "profile": "Auto profile for isolation test.",
            "architecture": "Auto architecture.",
            "status": "Auto status.",
            "constraints": "Auto constraints.",
            "testing": "Auto testing protocol.",
            "knowledge": "Auto lessons.",
            "force": True,
        },
    }))

    payload = _tool_payload(await _post(client, "tools/call", {
        "name": "memory_search",
        "arguments": {"project": "wrong_project_slug", "query": "project_profile"},
    }))
    assert payload["memories"] == []


@pytest.mark.asyncio
async def test_mcp_bootstrap_capsule_retrieval_queries(client):
    """Capsule label queries retrieve expected capsule types."""
    project = "bootstrap_test_capsule_queries"
    _tool_payload(await _post(client, "tools/call", {
        "name": "project_bootstrap",
        "arguments": {
            "project": project,
            "repo_path": "/tmp/repo",
            "profile": "Profile for capsule query test.",
            "architecture": "Architecture for capsule query test.",
            "status": "Status for capsule query test.",
            "constraints": "Constraints for capsule query test.",
            "testing": "Testing protocol for capsule query test.",
            "knowledge": "Knowledge for capsule query test.",
            "force": True,
        },
    }))

    architecture = _tool_payload(await _post(client, "tools/call", {
        "name": "memory_search",
        "arguments": {"project": project, "query": "architecture"},
    }))
    assert any(m.get("capsule_type") == "architecture_summary" for m in architecture["memories"])

    testing = _tool_payload(await _post(client, "tools/call", {
        "name": "memory_recall",
        "arguments": {"project": project, "query": "testing protocol"},
    }))
    assert any(m.get("capsule_type") == "testing_protocol" for m in testing["hits"])


# ── P19-11: dotted legacy aliases are accepted but not advertised ─────────────

@pytest.mark.asyncio
async def test_mcp_dotted_alias_accepted(client):
    """Legacy dotted names (memory.remember) work but are not in tools/list."""
    # Dotted name must not appear in tools/list
    r = await _post(client, "tools/list")
    data = _decode_sse(r.text)
    names = {t["name"] for t in data["result"]["tools"]}
    assert "memory.remember" not in names

    # But dotted name must still be callable as a legacy alias
    r2 = await _post(client, "tools/call", {
        "name": "memory.remember",
        "arguments": {
            "type": "fact",
            "content": "P19.11 legacy alias test",
            "project": "p19_legacy_alias",
        },
    })
    data2 = _decode_sse(r2.text)
    assert "result" in data2, f"Dotted alias rejected: {data2}"
    payload = json.loads(data2["result"]["content"][0]["text"])
    assert payload.get("ok") is True
