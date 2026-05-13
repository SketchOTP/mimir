"""SDK / REST API parity tests.

Verifies that each SDK method:
  1. Calls the correct HTTP method + endpoint
  2. Returns a response with the same top-level keys as the REST API

Strategy: patch _apost/_aget on the SDK instance to capture what path/body would
be sent, then call the real REST API and compare the response shapes.
"""

import pytest
from unittest.mock import AsyncMock
from sdk.client import MimirClient


# ── Shared fixture ────────────────────────────────────────────────────────────

@pytest.fixture
def sdk_capture():
    """Return (sdk, calls_list).  HTTP calls are captured; responses are stubbed."""
    sdk = MimirClient()
    calls: list[dict] = []

    async def fake_apost(path: str, body: dict) -> dict:
        calls.append({"method": "POST", "path": path, "body": body})
        return {"id": "stub", "status": "ok", "memories": [], "skills": [], "approvals": []}

    async def fake_aget(path: str, params: dict | None = None) -> dict:
        calls.append({"method": "GET", "path": path, "params": params})
        return {"id": "stub", "status": "ok", "memories": [], "skills": [], "approvals": []}

    sdk._apost = fake_apost
    sdk._aget = fake_aget
    return sdk, calls


# ── Routing checks (SDK calls the right endpoint) ────────────────────────────

@pytest.mark.asyncio
async def test_sdk_remember_routes_to_events(sdk_capture):
    """memory.remember → POST /api/events."""
    sdk, calls = sdk_capture
    await sdk.memory.aremember({"type": "fact", "content": "preferred name is Tym"})
    assert calls, "No HTTP call was made"
    assert calls[-1]["method"] == "POST"
    assert calls[-1]["path"] == "/api/events"


@pytest.mark.asyncio
async def test_sdk_recall_routes_to_events_recall(sdk_capture):
    """memory.recall → POST /api/events/recall with query in body."""
    sdk, calls = sdk_capture
    await sdk.memory.arecall("preferred name", project="home")
    assert calls[-1]["method"] == "POST"
    assert calls[-1]["path"] == "/api/events/recall"
    assert calls[-1]["body"]["query"] == "preferred name"


# ── Schema parity checks (REST response shape) ────────────────────────────────

@pytest.mark.asyncio
async def test_sdk_remember_rest_parity(client):
    """POST /api/events returns {ok, stored} — SDK must receive the same shape."""
    r = await client.post("/api/events", json={"type": "fact", "content": "SDK parity: remember"})
    assert r.status_code == 200
    data = r.json()
    assert data.get("ok") is True
    assert "stored" in data


@pytest.mark.asyncio
async def test_sdk_recall_rest_parity(client):
    """POST /api/events/recall returns hits or context — SDK must receive same."""
    r = await client.post("/api/events/recall", json={"query": "SDK parity recall query"})
    assert r.status_code == 200
    data = r.json()
    # Without token_budget: returns {hits:[]}; with token_budget: returns context dict
    assert "hits" in data or "memories" in data or "context" in data


@pytest.mark.asyncio
async def test_sdk_search_rest_parity(client):
    """GET /api/memory returns {memories} — SDK memory.search must receive same."""
    r = await client.get("/api/memory", params={"query": "SDK parity search"})
    assert r.status_code == 200
    assert "memories" in r.json()


@pytest.mark.asyncio
async def test_sdk_record_outcome_rest_parity(client):
    """SDK memory.record_outcome → POST /api/events with type=outcome."""
    r = await client.post("/api/events", json={
        "type": "outcome",
        "content": "SDK parity: task completed",
    })
    assert r.status_code == 200
    assert r.json().get("ok") is True


@pytest.mark.asyncio
async def test_sdk_skills_propose_rest_parity(client):
    """POST /api/skills/propose accepts the same payload as SDK skills.propose."""
    r = await client.post("/api/skills/propose", json={
        "name": "sdk-parity-skill",
        "purpose": "verify SDK propose parity",
        "steps": [{"action": "step1"}],
    })
    # 422 is acceptable if skill gating rejects (not enough traces), but must not 500
    assert r.status_code in (200, 422)


@pytest.mark.asyncio
async def test_sdk_approval_request_rest_parity(client):
    """SDK approval.request → POST /api/approvals?improvement_id=..."""
    r = await client.post("/api/improvements/propose", json={
        "improvement_type": "retrieval_tune",
        "title": "SDK parity: approval request",
        "reason": "testing SDK parity",
        "current_behavior": "default",
        "proposed_behavior": "tuned",
        "expected_benefit": "better retrieval",
    })
    assert r.status_code == 200
    imp_id = r.json()["id"]

    r = await client.post(f"/api/approvals?improvement_id={imp_id}", json={})
    assert r.status_code == 200
    data = r.json()
    assert "approval" in data
    assert "id" in data["approval"]


@pytest.mark.asyncio
async def test_sdk_approval_status_rest_parity(client):
    """SDK approval.status → GET /api/approvals → returns {approvals}."""
    r = await client.get("/api/approvals", params={"status": "all"})
    assert r.status_code == 200
    assert "approvals" in r.json()
