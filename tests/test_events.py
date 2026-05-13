"""Tests for event ingestion and recall endpoints."""

import pytest


@pytest.mark.asyncio
async def test_ingest_event(client):
    r = await client.post("/api/events", json={
        "type": "user_correction",
        "correction": "Call me Tym",
        "project": "test_project",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert len(data["stored"]) >= 1


@pytest.mark.asyncio
async def test_recall(client):
    # First store something
    await client.post("/api/memory", json={
        "content": "The preferred name is Tym",
        "layer": "semantic",
        "project": "test_project",
        "importance": 0.9,
    })

    r = await client.post("/api/events/recall", json={
        "query": "what is the user's name",
        "project": "test_project",
    })
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
