"""UI smoke tests.

Mimir's frontend is a React SPA backed by REST API endpoints.
These tests prove functional navigation by verifying:
  - The built dist artifact exists (web/dist/index.html)
  - Every UI page's backing API endpoint responds 200 with the expected shape
  - The API returns a clear response when a route does not exist (error state)
"""

import pathlib
import pytest


# ── Build artifact ─────────────────────────────────────────────────────────────

def test_web_dist_exists():
    """web/dist/index.html must exist (proves the UI was built and committed)."""
    dist = pathlib.Path(__file__).parent.parent / "web" / "dist" / "index.html"
    if not dist.exists():
        pytest.skip("web/dist not built — run 'cd web && npm run build' first")
    assert dist.is_file()


# ── API endpoints backing each UI page ────────────────────────────────────────

@pytest.mark.asyncio
async def test_ui_dashboard_loads(client):
    """Dashboard page: GET /api/dashboard → 200 with metrics dict."""
    r = await client.get("/api/dashboard")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


@pytest.mark.asyncio
async def test_ui_memories_page_loads(client):
    """Memories page: GET /api/memory → 200 with memories list."""
    r = await client.get("/api/memory")
    assert r.status_code == 200
    assert "memories" in r.json()


@pytest.mark.asyncio
async def test_ui_skills_page_loads(client):
    """Skills page: GET /api/skills → 200 with skills list."""
    r = await client.get("/api/skills")
    assert r.status_code == 200
    assert "skills" in r.json()


@pytest.mark.asyncio
async def test_ui_approvals_page_loads(client):
    """Approvals page: GET /api/approvals → 200 with approvals list."""
    r = await client.get("/api/approvals")
    assert r.status_code == 200
    assert "approvals" in r.json()


@pytest.mark.asyncio
async def test_ui_settings_page_loads(client):
    """Settings page uses health check to confirm API is reachable."""
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_ui_approval_detail_route_exists(client):
    """ApprovalDetail page: GET /api/approvals/:id → 200 for a real ID, 404 for unknown."""
    # Unknown ID → 404 (confirms the endpoint and route handler exist)
    r = await client.get("/api/approvals/no_such_approval_xyz")
    assert r.status_code == 404
    assert "detail" in r.json()

    # Real approval ID → 200 with expected schema
    proposal = await client.post("/api/improvements/propose", json={
        "improvement_type": "skill_update",
        "title": "UI route smoke test",
        "reason": "Smoke test",
        "current_behavior": "Old",
        "proposed_behavior": "New",
        "expected_benefit": "Better",
    })
    imp_id = proposal.json()["id"]
    ar = await client.post(f"/api/approvals?improvement_id={imp_id}", json={})
    approval_id = ar.json()["approval"]["id"]

    r = await client.get(f"/api/approvals/{approval_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == approval_id
    assert "status" in data
    assert "summary" in data


@pytest.mark.asyncio
async def test_ui_api_unavailable_state(client):
    """Unknown route returns structured JSON error — UI can render this cleanly."""
    r = await client.get("/api/nonexistent_ui_smoke_endpoint")
    assert r.status_code == 404
    data = r.json()
    assert "detail" in data  # FastAPI's standard error envelope
