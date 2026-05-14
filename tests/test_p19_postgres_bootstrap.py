from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

_BEARER = {"Authorization": "Bearer local-dev-key", "Accept": "application/json, text/event-stream"}


def _rpc(method: str, params: dict | None = None, req_id: int | None = 1) -> dict:
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if req_id is not None:
        msg["id"] = req_id
    if params is not None:
        msg["params"] = params
    return msg


def _decode_sse(text: str) -> dict:
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    raise AssertionError(f"No data payload found: {text!r}")


def _tool_payload(resp) -> dict:
    data = _decode_sse(resp.text)
    return json.loads(data["result"]["content"][0]["text"])


async def _post(client: AsyncClient, method: str, params: dict | None = None) -> dict:
    r = await client.post("/mcp", json=_rpc(method, params), headers=_BEARER)
    assert r.status_code == 200, r.text
    return r


@pytest.fixture(scope="module")
def postgres_url() -> str:
    url = os.environ.get("MIMIR_TEST_POSTGRES_URL", "").strip()
    if not url:
        pytest.skip("MIMIR_TEST_POSTGRES_URL not set")
    return url


@pytest.fixture
async def pg_client(postgres_url, tmp_path):
    from mimir.config import get_settings
    from storage import vector_store
    from storage.database import get_session_factory, init_db
    from storage.models import Base
    from storage.search_backend import reset_search_backend

    data_dir = tmp_path / "pg-data"
    vector_dir = tmp_path / "pg-vectors"
    data_dir.mkdir(parents=True, exist_ok=True)
    if vector_dir.exists():
        shutil.rmtree(vector_dir)

    os.environ["MIMIR_DATABASE_URL"] = postgres_url
    os.environ["MIMIR_DATA_DIR"] = str(data_dir)
    os.environ["MIMIR_VECTOR_DIR"] = str(vector_dir)
    os.environ["MIMIR_ENV"] = "development"

    get_settings.cache_clear()
    reset_search_backend()

    import storage.database as database

    if database._engine is not None:
        await database._engine.dispose()
    database._engine = None
    database._session_factory = None

    vector_store._client = None

    await init_db()
    factory = get_session_factory()
    async with factory() as session:
        bind = session.bind
        assert bind is not None
        async with bind.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    from api.main import app as fastapi_app

    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as client:
        yield client

    if database._engine is not None:
        await database._engine.dispose()
    database._engine = None
    database._session_factory = None
    get_settings.cache_clear()
    reset_search_backend()
    vector_store._client = None


def _bootstrap_args(project: str) -> dict:
    return {
        "project": project,
        "repo_path": "/home/sketch/auto",
        "force": True,
        "profile": "Auto is the project identity capsule.",
        "architecture": "Auto uses a service and UI split architecture.",
        "status": "Auto is actively being integrated.",
        "constraints": "Never delete trusted memories silently.",
        "testing": "Run pytest tests/ before completion.",
        "knowledge": "When bootstrap changes, reindex vectors and verify recall.",
    }


@pytest.mark.asyncio
async def test_postgres_bootstrap_stores_rows_and_vector_metadata(pg_client):
    from storage.database import get_session_factory
    from storage.models import Memory
    from storage.vector_store import _collection

    project = "auto"
    payload = _tool_payload(await _post(pg_client, "tools/call", {
        "name": "project_bootstrap",
        "arguments": _bootstrap_args(project),
    }))
    assert payload["ok"] is True
    assert payload["missing_capsule_types"] == []
    assert len(payload["stored"]) == 7

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(Memory).where(
                Memory.project == project,
                Memory.source_type == "project_bootstrap",
                Memory.deleted_at.is_(None),
            )
        )
        memories = list(result.scalars())

    assert len(memories) == 7
    capsule_types = {m.meta.get("capsule_type") for m in memories if isinstance(m.meta, dict)}
    assert capsule_types == {
        "project_profile",
        "architecture_summary",
        "active_status",
        "safety_constraint",
        "testing_protocol",
        "procedural_lesson",
        "governance_rules",
    }
    for mem in memories:
        assert mem.project == project
        assert isinstance(mem.meta, dict) and mem.meta.get("bootstrap") is True
        assert mem.memory_state == "active"
        assert mem.source_type == "project_bootstrap"
        assert (mem.trust_score or 0.0) >= 0.8
        assert mem.verification_status == "trusted_system_observed"

        vector_row = _collection(mem.layer).get(ids=[mem.id], include=["metadatas"])
        meta = vector_row["metadatas"][0]
        assert meta["project"] == project
        assert meta["project_id"] == project
        assert meta["capsule_type"] == mem.meta["capsule_type"]
        assert meta["source_type"] == "project_bootstrap"
        assert meta["memory_state"] == "active"


@pytest.mark.asyncio
async def test_postgres_bootstrap_memory_search_exact_labels(pg_client):
    _tool_payload(await _post(pg_client, "tools/call", {
        "name": "project_bootstrap",
        "arguments": _bootstrap_args("auto"),
    }))

    q1 = _tool_payload(await _post(pg_client, "tools/call", {
        "name": "memory_search",
        "arguments": {"project": "auto", "query": "project_profile", "min_score": 0.0},
    }))
    assert any(m.get("capsule_type") == "project_profile" for m in q1["memories"])

    q2 = _tool_payload(await _post(pg_client, "tools/call", {
        "name": "memory_search",
        "arguments": {"project": "auto", "query": "architecture_summary", "min_score": 0.0},
    }))
    assert any(m.get("capsule_type") == "architecture_summary" for m in q2["memories"])

    q3 = _tool_payload(await _post(pg_client, "tools/call", {
        "name": "memory_search",
        "arguments": {"project": "auto", "query": "testing_protocol", "min_score": 0.0},
    }))
    assert any(m.get("capsule_type") == "testing_protocol" for m in q3["memories"])


@pytest.mark.asyncio
async def test_postgres_bootstrap_memory_recall_intents(pg_client):
    _tool_payload(await _post(pg_client, "tools/call", {
        "name": "project_bootstrap",
        "arguments": _bootstrap_args("auto"),
    }))

    identity = _tool_payload(await _post(pg_client, "tools/call", {
        "name": "memory_recall",
        "arguments": {"project": "auto", "query": "what is this project?", "min_score": 0.0},
    }))
    identity_capsules = {hit.get("capsule_type") for hit in identity["hits"]}
    assert "project_profile" in identity_capsules
    assert "architecture_summary" in identity_capsules
    assert {
        "project_profile",
        "architecture_summary",
        "active_status",
        "safety_constraint",
        "testing_protocol",
        "procedural_lesson",
        "governance_rules",
    }.issubset(identity_capsules)

    testing = _tool_payload(await _post(pg_client, "tools/call", {
        "name": "memory_recall",
        "arguments": {"project": "auto", "query": "what tests should I run?", "min_score": 0.0},
    }))
    assert any(hit.get("capsule_type") == "testing_protocol" for hit in testing["hits"])


@pytest.mark.asyncio
async def test_postgres_bootstrap_wrong_project_isolated(pg_client):
    _tool_payload(await _post(pg_client, "tools/call", {
        "name": "project_bootstrap",
        "arguments": _bootstrap_args("auto"),
    }))

    search = _tool_payload(await _post(pg_client, "tools/call", {
        "name": "memory_search",
        "arguments": {"project": "wrong-project", "query": "project_profile", "min_score": 0.0},
    }))
    assert search["memories"] == []

    recall = _tool_payload(await _post(pg_client, "tools/call", {
        "name": "memory_recall",
        "arguments": {"project": "wrong-project", "query": "what is this project?", "min_score": 0.0},
    }))
    assert recall["hits"] == []
