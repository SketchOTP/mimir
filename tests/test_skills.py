"""Tests for skill lifecycle."""

import pytest


@pytest.mark.asyncio
async def test_propose_and_run_skill(client):
    # Propose
    r = await client.post("/api/skills/propose", json={
        "name": "Test Skill",
        "purpose": "Testing the skill system",
        "steps": [{"order": 1, "action": "test_action"}],
        "project": "test",
    })
    assert r.status_code == 200
    skill_id = r.json()["id"]

    # Get
    r = await client.get(f"/api/skills/{skill_id}")
    assert r.status_code == 200
    assert r.json()["name"] == "Test Skill"

    # Run
    r = await client.post(f"/api/skills/{skill_id}/run", json={"input_data": {"test": True}})
    assert r.status_code == 200
    assert "run_id" in r.json()

    # Test
    r = await client.post(f"/api/skills/{skill_id}/test")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_list_skills(client):
    r = await client.get("/api/skills")
    assert r.status_code == 200
    assert "skills" in r.json()
