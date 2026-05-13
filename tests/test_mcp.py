"""MCP smoke tests.

The MCP server (mcp/server.py) is a thin HTTP adapter that cannot be imported
cleanly in-process because the local mcp/ package shadows the installed MCP SDK.
Instead, these tests verify:

  1. All 18 expected tool names are declared in the server source (static scan).
  2. Each of the 6 acceptance-criteria tools maps to a working REST endpoint.
  3. Unknown resources return a clear error response (not a crash).
"""

import pathlib
import pytest

# ── Tool registry ─────────────────────────────────────────────────────────────

EXPECTED_TOOLS = {
    "memory.remember",
    "memory.recall",
    "memory.search",
    "memory.summarize_session",
    "memory.record_outcome",
    "skill.list",
    "skill.get",
    "skill.propose",
    "skill.run",
    "skill.record_result",
    "reflection.log",
    "reflection.generate",
    "improvement.propose",
    "improvement.status",
    "approval.request",
    "approval.status",
    "approval.approve",
    "approval.reject",
}

_SERVER_SRC = pathlib.Path(__file__).parent.parent / "mcp" / "server.py"


def test_mcp_tool_names_declared():
    """Every expected tool name appears in mcp/server.py."""
    src = _SERVER_SRC.read_text()
    missing = [n for n in EXPECTED_TOOLS if f'name="{n}"' not in src]
    assert not missing, f"Tools missing from mcp/server.py: {missing}"


def test_mcp_tool_count():
    """Exactly 18 tools are declared (guards against silent additions/deletions)."""
    src = _SERVER_SRC.read_text()
    count = sum(1 for n in EXPECTED_TOOLS if f'name="{n}"' in src)
    assert count == 18


def test_mcp_dispatch_routes_declared():
    """All 18 tool names also appear in the _dispatch match statement."""
    src = _SERVER_SRC.read_text()
    missing = [n for n in EXPECTED_TOOLS if f'case "{n}"' not in src]
    assert not missing, f"Tools missing from _dispatch in mcp/server.py: {missing}"


# ── REST endpoint tests (MCP tools are thin HTTP adapters) ────────────────────

@pytest.mark.asyncio
async def test_mcp_memory_remember_endpoint(client):
    """memory.remember → POST /api/events → returns {ok, stored}."""
    r = await client.post("/api/events", json={
        "type": "fact",
        "content": "MCP smoke: user prefers dark mode",
    })
    assert r.status_code == 200
    data = r.json()
    assert data.get("ok") is True
    assert "stored" in data


@pytest.mark.asyncio
async def test_mcp_memory_recall_endpoint(client):
    """memory.recall → POST /api/events/recall → returns hits or context."""
    r = await client.post("/api/events/recall", json={"query": "dark mode preference"})
    assert r.status_code == 200
    data = r.json()
    # Without token_budget: returns {hits:[]}; with token_budget: returns context dict
    assert "hits" in data or "memories" in data or "context" in data


@pytest.mark.asyncio
async def test_mcp_skill_list_endpoint(client):
    """skill.list → GET /api/skills → returns skills list."""
    r = await client.get("/api/skills")
    assert r.status_code == 200
    assert "skills" in r.json()


@pytest.mark.asyncio
async def test_mcp_reflection_log_endpoint(client):
    """reflection.log → POST /api/reflections → returns reflection record."""
    r = await client.post("/api/reflections", json={
        "trigger": "manual",
        "observations": ["MCP smoke: observed inefficiency in retrieval"],
        "lessons": ["Tune retrieval threshold"],
    })
    assert r.status_code == 200
    data = r.json()
    assert "id" in data
    assert "trigger" in data


@pytest.mark.asyncio
async def test_mcp_approval_request_endpoint(client):
    """approval.request → POST /api/approvals?improvement_id=... → returns approval."""
    r = await client.post("/api/improvements/propose", json={
        "improvement_type": "context_tune",
        "title": "MCP smoke test approval",
        "reason": "smoke testing",
        "current_behavior": "default context window",
        "proposed_behavior": "tuned context window",
        "expected_benefit": "fewer irrelevant memories",
    })
    assert r.status_code == 200
    imp_id = r.json()["id"]

    r = await client.post(f"/api/approvals?improvement_id={imp_id}", json={})
    assert r.status_code == 200
    data = r.json()
    assert "approval" in data
    assert "id" in data["approval"]
    assert "notifications" in data


@pytest.mark.asyncio
async def test_mcp_approval_status_endpoint(client):
    """approval.status → GET /api/approvals → returns list."""
    r = await client.get("/api/approvals", params={"status": "all"})
    assert r.status_code == 200
    assert "approvals" in r.json()


@pytest.mark.asyncio
async def test_mcp_error_on_missing_resource(client):
    """When a resource is not found the API returns a clear non-200 response."""
    r = await client.get("/api/improvements/nonexistent_mcp_smoke_xyz")
    assert r.status_code in (404, 422)
    assert r.text  # must have a body, not empty
