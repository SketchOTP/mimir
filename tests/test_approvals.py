"""Tests for the approval workflow."""

import pytest


async def _create_approval(client) -> str:
    r = await client.post("/api/improvements/propose", json={
        "improvement_type": "retrieval_tune",
        "title": "Detail test improvement",
        "reason": "Testing GET by ID",
        "current_behavior": "Default behavior",
        "proposed_behavior": "Improved behavior",
        "expected_benefit": "Better results",
    })
    imp_id = r.json()["id"]
    r = await client.post(f"/api/approvals?improvement_id={imp_id}", json={})
    return r.json()["approval"]["id"]


@pytest.mark.asyncio
async def test_full_approval_flow(client):
    # Propose improvement
    r = await client.post("/api/improvements/propose", json={
        "improvement_type": "retrieval_tune",
        "title": "Test improvement",
        "reason": "Testing the approval flow",
        "current_behavior": "Retrieval uses default settings",
        "proposed_behavior": "Retrieval uses tuned thresholds",
        "expected_benefit": "Better relevance",
    })
    assert r.status_code == 200
    imp_id = r.json()["id"]

    # Create approval request
    r = await client.post(f"/api/approvals?improvement_id={imp_id}", json={})
    assert r.status_code == 200
    approval_id = r.json()["approval"]["id"]

    # List approvals
    r = await client.get("/api/approvals")
    assert r.status_code == 200
    assert any(a["id"] == approval_id for a in r.json()["approvals"])

    # Approve
    r = await client.post(f"/api/approvals/{approval_id}/approve", json={"reviewer_note": "LGTM"})
    assert r.status_code == 200
    assert r.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_reject_approval(client):
    r = await client.post("/api/improvements/propose", json={
        "improvement_type": "memory_policy",
        "title": "Risky change",
        "reason": "Testing rejection",
        "current_behavior": "Default",
        "proposed_behavior": "Risky change",
        "expected_benefit": "Unknown",
        "risk": "high",
    })
    imp_id = r.json()["id"]

    r = await client.post(f"/api/approvals?improvement_id={imp_id}", json={})
    approval_id = r.json()["approval"]["id"]

    r = await client.post(f"/api/approvals/{approval_id}/reject", json={"reviewer_note": "Too risky"})
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"


@pytest.mark.asyncio
async def test_get_approval_by_id_success(client):
    approval_id = await _create_approval(client)

    r = await client.get(f"/api/approvals/{approval_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == approval_id
    assert data["status"] == "pending"
    assert "title" in data
    assert "summary" in data
    assert "created_at" in data


@pytest.mark.asyncio
async def test_get_approval_by_id_not_found(client):
    r = await client.get("/api/approvals/nonexistent_id_xyz_999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_approval_by_id_includes_decision_fields_after_approve(client):
    approval_id = await _create_approval(client)

    await client.post(f"/api/approvals/{approval_id}/approve", json={"reviewer_note": "From mobile"})

    r = await client.get(f"/api/approvals/{approval_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "approved"
    assert data["reviewer_note"] == "From mobile"


@pytest.mark.asyncio
async def test_approve_from_detail_page_uses_existing_flow(client):
    approval_id = await _create_approval(client)

    # Simulate PWA detail page: approve via the standard endpoint
    r = await client.post(f"/api/approvals/{approval_id}/approve", json={"reviewer_note": "PWA approve"})
    assert r.status_code == 200
    assert r.json()["status"] == "approved"

    # Finalized approval is reflected in GET
    r = await client.get(f"/api/approvals/{approval_id}")
    assert r.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_finalized_approval_cannot_be_approved_again(client):
    approval_id = await _create_approval(client)

    await client.post(f"/api/approvals/{approval_id}/approve", json={})

    # Second approve on a decided approval should return 404 (not pending)
    r = await client.post(f"/api/approvals/{approval_id}/approve", json={})
    assert r.status_code == 404
