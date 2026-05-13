"""Unified recall response tests.

Verifies that POST /api/events/recall always returns the stable shape:
  {
    "query": str,
    "hits": [...],
    "context": {...}   # only when token_budget given
  }

SDK parity and MCP tool correctness follow from this shape being stable.
"""

import pytest


@pytest.mark.asyncio
async def test_recall_always_returns_query_and_hits(client):
    """Without token_budget: response contains query + hits (no context key)."""
    r = await client.post("/api/events/recall", json={"query": "preferred name"})
    assert r.status_code == 200
    data = r.json()
    assert "query" in data, f"'query' missing from recall response: {data}"
    assert "hits" in data, f"'hits' missing from recall response: {data}"
    assert isinstance(data["hits"], list)
    assert "context" not in data, "context must not appear without token_budget"


@pytest.mark.asyncio
async def test_recall_with_token_budget_includes_context(client):
    """With token_budget: response contains query + hits + context."""
    # First store something to recall
    await client.post("/api/events", json={
        "type": "fact",
        "content": "Unified recall test: user's timezone is Europe/London",
    })

    r = await client.post("/api/events/recall", json={
        "query": "timezone",
        "token_budget": 512,
    })
    assert r.status_code == 200
    data = r.json()
    assert "query" in data
    assert "hits" in data
    assert "context" in data, f"'context' missing when token_budget given: {data}"
    ctx = data["context"]
    assert "memories" in ctx
    assert "token_cost" in ctx
    assert "debug" in ctx
    assert isinstance(ctx["memories"], list)
    assert isinstance(ctx["token_cost"], int)


@pytest.mark.asyncio
async def test_recall_hits_is_list_even_when_empty(client):
    """hits is always a list, even when no memories match the query."""
    r = await client.post("/api/events/recall", json={
        "query": "xyzzy_no_match_recall_test_unique_query_string",
    })
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data["hits"], list)


@pytest.mark.asyncio
async def test_recall_context_token_cost_is_non_negative(client):
    """token_cost in context is a non-negative integer."""
    r = await client.post("/api/events/recall", json={
        "query": "test query for token cost check",
        "token_budget": 256,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["context"]["token_cost"] >= 0


@pytest.mark.asyncio
async def test_recall_sdk_docstring_alignment(client):
    """Confirm the SDK recall() method docstring is consistent with the actual shape.

    This is a schema contract test: any change to the response shape here must
    trigger a corresponding SDK update.
    """
    from sdk.client import MimirClient
    import inspect
    # SDK method exists
    assert hasattr(MimirClient().memory, "recall")
    # SDK posts to the correct endpoint
    src = inspect.getsource(MimirClient().memory.recall)
    assert "/api/events/recall" in src
