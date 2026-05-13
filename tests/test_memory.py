"""Tests for memory extraction, classification, and CRUD."""

import pytest
from memory.memory_extractor import classify, extract_importance, extract_from_event


def test_classify_semantic():
    assert classify("I prefer dark mode always") == "semantic"
    assert classify("Call me Tym, never Timothy") == "semantic"


def test_classify_procedural():
    assert classify("Step 1: run the tests. Step 2: deploy.") == "procedural"


def test_classify_episodic():
    assert classify("Right now the server is down") == "episodic"
    assert classify("The API returned an error") == "episodic"


def test_importance_user_correction():
    score = extract_importance("wrong name used", "user_correction")
    assert score > 0.5


def test_extract_from_event_user_correction():
    event = {"type": "user_correction", "correction": "Call me Tym, not Timothy", "project": "home"}
    candidates = extract_from_event(event)
    assert any(c["layer"] == "semantic" and c["importance"] >= 0.9 for c in candidates)


def test_extract_from_event_outcome():
    event = {"type": "outcome", "result": "success", "lesson": "Always validate input first"}
    candidates = extract_from_event(event)
    assert len(candidates) >= 1


@pytest.mark.asyncio
async def test_memory_api_crud(client):
    # Create
    r = await client.post("/api/memory", json={"content": "User prefers dark mode", "layer": "semantic"})
    assert r.status_code == 200
    mem_id = r.json()["id"]

    # Get
    r = await client.get(f"/api/memory/{mem_id}")
    assert r.status_code == 200
    assert r.json()["layer"] == "semantic"

    # List
    r = await client.get("/api/memory")
    assert r.status_code == 200
    assert len(r.json()["memories"]) >= 1

    # Patch
    r = await client.patch(f"/api/memory/{mem_id}", json={"importance": 0.9})
    assert r.status_code == 200

    # Delete
    r = await client.delete(f"/api/memory/{mem_id}")
    assert r.status_code == 200
    assert r.json()["ok"] is True
