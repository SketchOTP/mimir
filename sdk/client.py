"""Mimir Python SDK — synchronous and async client."""

from __future__ import annotations

from typing import Any

import httpx


class MimirClient:
    """Sync/async client for the Mimir REST API."""

    def __init__(self, base_url: str = "http://127.0.0.1:8787", api_key: str = "local-dev-key"):
        self._base = base_url.rstrip("/")
        self._headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        self.memory = _MemoryAPI(self)
        self.skills = _SkillsAPI(self)
        self.approval = _ApprovalAPI(self)

    # ── sync helpers ─────────────────────────────────────────────────────────

    def _post(self, path: str, body: dict) -> dict:
        r = httpx.post(f"{self._base}{path}", json=body, headers=self._headers, timeout=30)
        r.raise_for_status()
        return r.json()

    def _get(self, path: str, params: dict | None = None) -> dict:
        r = httpx.get(f"{self._base}{path}", params=params, headers=self._headers, timeout=30)
        r.raise_for_status()
        return r.json()

    # ── async helpers ─────────────────────────────────────────────────────────

    async def _apost(self, path: str, body: dict) -> dict:
        async with httpx.AsyncClient(base_url=self._base, timeout=30) as client:
            r = await client.post(path, json=body, headers=self._headers)
            r.raise_for_status()
            return r.json()

    async def _aget(self, path: str, params: dict | None = None) -> dict:
        async with httpx.AsyncClient(base_url=self._base, timeout=30) as client:
            r = await client.get(path, params=params, headers=self._headers)
            r.raise_for_status()
            return r.json()


class _MemoryAPI:
    def __init__(self, client: MimirClient):
        self._c = client

    def remember(
        self, event: dict[str, Any], *, project: str | None = None, session_id: str | None = None
    ) -> dict:
        return self._c._post("/api/events", {**event, "project": project, "session_id": session_id})

    def recall(
        self,
        query: str,
        *,
        scope: str | None = None,
        project: str | None = None,
        session_id: str | None = None,
        token_budget: int | None = None,
        limit: int = 10,
    ) -> dict:
        body = {"query": query, "project": project or scope, "session_id": session_id, "limit": limit}
        if token_budget:
            body["token_budget"] = token_budget
        return self._c._post("/api/events/recall", body)

    def search(self, query: str, layer: str | None = None, project: str | None = None) -> dict:
        params = {"query": query}
        if layer:
            params["layer"] = layer
        if project:
            params["project"] = project
        return self._c._get("/api/memory", params)

    def summarize_session(self, session_id: str, project: str | None = None) -> dict:
        return self._c._post("/api/reflections/generate", {"session_id": session_id, "project": project})

    def record_outcome(self, result: dict[str, Any]) -> dict:
        return self._c._post("/api/events", {"type": "outcome", **result})

    # Async variants
    async def aremember(self, event: dict, **kwargs) -> dict:
        return await self._c._apost("/api/events", {**event, **kwargs})

    async def arecall(self, query: str, **kwargs) -> dict:
        return await self._c._apost("/api/events/recall", {"query": query, **kwargs})


class _SkillsAPI:
    def __init__(self, client: MimirClient):
        self._c = client

    def propose(self, trace: dict[str, Any]) -> dict:
        return self._c._post("/api/skills/propose", trace)

    def list(self, project: str | None = None, status: str | None = None) -> dict:
        params = {}
        if project:
            params["project"] = project
        if status:
            params["status"] = status
        return self._c._get("/api/skills", params)

    def run(self, skill_id: str, input_data: dict | None = None) -> dict:
        return self._c._post(f"/api/skills/{skill_id}/run", {"input_data": input_data})

    def record_result(self, skill_id: str, result: dict) -> dict:
        return self._c._post(f"/api/skills/{skill_id}/result", result)

    async def arun(self, skill_id: str, input_data: dict | None = None) -> dict:
        return await self._c._apost(f"/api/skills/{skill_id}/run", {"input_data": input_data})


class _ApprovalAPI:
    def __init__(self, client: MimirClient):
        self._c = client

    def request(self, improvement_id: str) -> dict:
        return self._c._post(f"/api/approvals?improvement_id={improvement_id}", {})

    def status(self, approval_id: str) -> dict:
        return self._c._get("/api/approvals", {"status": "all"})

    def approve(self, approval_id: str, note: str | None = None) -> dict:
        return self._c._post(f"/api/approvals/{approval_id}/approve", {"reviewer_note": note})

    def reject(self, approval_id: str, note: str | None = None) -> dict:
        return self._c._post(f"/api/approvals/{approval_id}/reject", {"reviewer_note": note})
