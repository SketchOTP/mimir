"""
Mimir MCP Server — thin adapter over the REST API.

Exposes MCP tools that delegate all logic to the Mimir HTTP service.
Run with: python -m mcp.server
"""

from __future__ import annotations

import json
import os

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

MIMIR_URL = os.environ.get("MIMIR_URL", "http://127.0.0.1:8787")
MIMIR_API_KEY = os.environ.get("MIMIR_API_KEY", "local-dev-key")

_HEADERS = {"X-API-Key": MIMIR_API_KEY, "Content-Type": "application/json"}

server = Server("mimir")


async def _post(path: str, body: dict) -> dict:
    async with httpx.AsyncClient(base_url=MIMIR_URL, timeout=30) as client:
        r = await client.post(path, json=body, headers=_HEADERS)
        r.raise_for_status()
        return r.json()


async def _get(path: str, params: dict | None = None) -> dict:
    async with httpx.AsyncClient(base_url=MIMIR_URL, timeout=30) as client:
        r = await client.get(path, params=params, headers=_HEADERS)
        r.raise_for_status()
        return r.json()


TOOLS: list[Tool] = [
    Tool(name="memory.remember", description="Store an event or fact in Mimir memory",
         inputSchema={"type": "object", "properties": {
             "type": {"type": "string"}, "content": {"type": "string"},
             "project": {"type": "string"}, "session_id": {"type": "string"},
         }, "required": ["type", "content"]}),

    Tool(name="memory.recall", description="Retrieve relevant memories for a query",
         inputSchema={"type": "object", "properties": {
             "query": {"type": "string"}, "project": {"type": "string"},
             "session_id": {"type": "string"}, "limit": {"type": "integer"},
             "token_budget": {"type": "integer"},
         }, "required": ["query"]}),

    Tool(name="memory.search", description="Semantic search across all memory layers",
         inputSchema={"type": "object", "properties": {
             "query": {"type": "string"}, "layer": {"type": "string"},
             "project": {"type": "string"}, "min_score": {"type": "number"},
         }, "required": ["query"]}),

    Tool(name="memory.summarize_session", description="Summarize and consolidate a session",
         inputSchema={"type": "object", "properties": {
             "session_id": {"type": "string"}, "project": {"type": "string"},
         }, "required": ["session_id"]}),

    Tool(name="memory.record_outcome", description="Record the outcome of a task",
         inputSchema={"type": "object", "properties": {
             "content": {"type": "string"}, "result": {"type": "string"},
             "lesson": {"type": "string"}, "project": {"type": "string"},
             "session_id": {"type": "string"},
         }, "required": ["content", "result"]}),

    Tool(name="skill.list", description="List available skills",
         inputSchema={"type": "object", "properties": {
             "project": {"type": "string"}, "status": {"type": "string"},
         }}),

    Tool(name="skill.get", description="Get a specific skill by ID",
         inputSchema={"type": "object", "properties": {
             "skill_id": {"type": "string"},
         }, "required": ["skill_id"]}),

    Tool(name="skill.propose", description="Propose a new skill",
         inputSchema={"type": "object", "properties": {
             "name": {"type": "string"}, "purpose": {"type": "string"},
             "steps": {"type": "array"}, "project": {"type": "string"},
         }, "required": ["name", "purpose"]}),

    Tool(name="skill.run", description="Execute a skill",
         inputSchema={"type": "object", "properties": {
             "skill_id": {"type": "string"}, "input_data": {"type": "object"},
         }, "required": ["skill_id"]}),

    Tool(name="skill.record_result", description="Record the result of a skill execution",
         inputSchema={"type": "object", "properties": {
             "skill_id": {"type": "string"}, "run_id": {"type": "string"},
             "outcome": {"type": "string"}, "output_data": {"type": "object"},
         }, "required": ["skill_id", "run_id", "outcome"]}),

    Tool(name="reflection.log", description="Log a reflection with observations and lessons",
         inputSchema={"type": "object", "properties": {
             "observations": {"type": "array"}, "lessons": {"type": "array"},
             "project": {"type": "string"},
         }, "required": ["observations", "lessons"]}),

    Tool(name="reflection.generate", description="Auto-generate a system reflection",
         inputSchema={"type": "object", "properties": {
             "project": {"type": "string"}, "window_hours": {"type": "integer"},
         }}),

    Tool(name="improvement.propose", description="Propose a system improvement",
         inputSchema={"type": "object", "properties": {
             "improvement_type": {"type": "string"}, "title": {"type": "string"},
             "reason": {"type": "string"}, "current_behavior": {"type": "string"},
             "proposed_behavior": {"type": "string"}, "expected_benefit": {"type": "string"},
             "project": {"type": "string"},
         }, "required": ["improvement_type", "title", "reason", "current_behavior",
                         "proposed_behavior", "expected_benefit"]}),

    Tool(name="improvement.status", description="Get status of an improvement proposal",
         inputSchema={"type": "object", "properties": {
             "improvement_id": {"type": "string"},
         }, "required": ["improvement_id"]}),

    Tool(name="approval.request", description="Create an approval request for an improvement",
         inputSchema={"type": "object", "properties": {
             "improvement_id": {"type": "string"},
         }, "required": ["improvement_id"]}),

    Tool(name="approval.status", description="Get approval status",
         inputSchema={"type": "object", "properties": {
             "approval_id": {"type": "string"},
         }, "required": ["approval_id"]}),

    Tool(name="approval.approve", description="Approve a pending request",
         inputSchema={"type": "object", "properties": {
             "approval_id": {"type": "string"}, "reviewer_note": {"type": "string"},
         }, "required": ["approval_id"]}),

    Tool(name="approval.reject", description="Reject a pending request",
         inputSchema={"type": "object", "properties": {
             "approval_id": {"type": "string"}, "reviewer_note": {"type": "string"},
         }, "required": ["approval_id"]}),
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        result = await _dispatch(name, arguments)
        return [TextContent(type="text", text=json.dumps(result, default=str))]
    except httpx.HTTPStatusError as e:
        return [TextContent(type="text", text=json.dumps({"error": str(e), "status": e.response.status_code}))]
    except Exception as e:
        return [TextContent(type="text", text=json.dumps({"error": str(e)}))]


async def _dispatch(name: str, args: dict) -> dict:
    match name:
        case "memory.remember":
            return await _post("/api/events", args)
        case "memory.recall":
            return await _post("/api/events/recall", args)
        case "memory.search":
            return await _get("/api/memory", args)
        case "memory.summarize_session":
            return await _post("/api/reflections/generate", args)
        case "memory.record_outcome":
            return await _post("/api/events", {"type": "outcome", **args})
        case "skill.list":
            return await _get("/api/skills", args)
        case "skill.get":
            sid = args.pop("skill_id")
            return await _get(f"/api/skills/{sid}")
        case "skill.propose":
            return await _post("/api/skills/propose", args)
        case "skill.run":
            sid = args.pop("skill_id")
            return await _post(f"/api/skills/{sid}/run", args)
        case "skill.record_result":
            sid = args.pop("skill_id")
            return await _post(f"/api/skills/{sid}/result", args)
        case "reflection.log":
            return await _post("/api/reflections", {"trigger": "manual", **args})
        case "reflection.generate":
            return await _post("/api/reflections/generate", args)
        case "improvement.propose":
            return await _post("/api/improvements/propose", args)
        case "improvement.status":
            iid = args.pop("improvement_id")
            return await _get(f"/api/improvements/{iid}")
        case "approval.request":
            iid = args.pop("improvement_id")
            return await _post(f"/api/approvals?improvement_id={iid}", {})
        case "approval.status":
            aid = args.pop("approval_id")
            return await _get(f"/api/approvals?status=all")
        case "approval.approve":
            aid = args.pop("approval_id")
            return await _post(f"/api/approvals/{aid}/approve", args)
        case "approval.reject":
            aid = args.pop("approval_id")
            return await _post(f"/api/approvals/{aid}/reject", args)
        case _:
            return {"error": f"Unknown tool: {name}"}


def run():
    import asyncio
    asyncio.run(stdio_server(server))


if __name__ == "__main__":
    run()
