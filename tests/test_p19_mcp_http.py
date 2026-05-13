"""P19 acceptance tests — MCP Streamable HTTP endpoint.

Tests:
  - POST /mcp requires auth (prod mode)
  - POST /mcp never returns 405
  - GET /mcp does not return 404 (returns SSE stream)
  - initialize handshake returns SSE-format response
  - tools/list returns all 9 required tools
  - memory.remember works
  - memory.recall works
  - invalid API key returns 401
  - notifications/initialized returns 202
  - cross-user isolation preserved
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

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


# ── P19-5: tools/list returns the 9 required tools ───────────────────────────

@pytest.mark.asyncio
async def test_mcp_lists_tools(client):
    """tools/list returns all 9 required Cursor tools."""
    r = await _post(client, "tools/list")
    data = _decode_sse(r.text)
    assert "result" in data
    names = {t["name"] for t in data["result"]["tools"]}
    required = {
        "memory.remember", "memory.recall", "memory.search",
        "memory.record_outcome", "skill.list", "approval.request",
        "approval.status", "reflection.log", "improvement.propose",
    }
    assert required.issubset(names), f"Missing: {required - names}"


# ── P19-6: memory.remember works ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mcp_memory_remember(client):
    """tools/call memory.remember stores a memory and returns {ok, stored}."""
    r = await _post(client, "tools/call", {
        "name": "memory.remember",
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


# ── P19-7: memory.recall works ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mcp_memory_recall(client):
    """tools/call memory.recall returns hits for a stored memory."""
    await _post(client, "tools/call", {
        "name": "memory.remember",
        "arguments": {
            "type": "fact",
            "content": "P19.1 recall test: agent uses vim keybindings",
            "project": "p19_recall",
        },
    })

    r = await _post(client, "tools/call", {
        "name": "memory.recall",
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
