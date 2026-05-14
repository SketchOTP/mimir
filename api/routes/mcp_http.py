"""MCP Streamable HTTP endpoint — exposes Mimir tools over JSON-RPC 2.0.

Mounted at /mcp (no /api prefix) so Cursor can connect with:
  URL:    http://<host>:8787/mcp
  Header: Authorization: Bearer <MIMIR_API_KEY>

MCP Streamable HTTP transport (2025-03-26 spec):
  POST /mcp  — client→server messages, responds with text/event-stream SSE
  GET  /mcp  — server→client SSE channel (keepalive; we don't push server events)
  DELETE /mcp — session cleanup (stateless: always 200)
  OPTIONS /mcp — handled by CORSMiddleware

Auth: Authorization: Bearer <key> or X-API-Key: <key>.
No local mcp/ package import — implements the protocol directly to avoid the
naming conflict with the installed mcp SDK.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any, AsyncIterator

from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from mimir.config import get_settings
from storage.database import get_session_factory

router = APIRouter(tags=["mcp"])
logger = logging.getLogger(__name__)

# Protocol version this server advertises
_MCP_VERSION = "2024-11-05"

# ── Tool registry ─────────────────────────────────────────────────────────────

_TOOLS = [
    {
        "name": "memory_remember",
        "description": "Store an event or fact in Mimir memory.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "description": "Memory type (fact, event, outcome, …)"},
                "content": {"type": "string", "description": "Memory content"},
                "project": {"type": "string"},
                "session_id": {"type": "string"},
            },
            "required": ["type", "content"],
        },
    },
    {
        "name": "memory_recall",
        "description": "Retrieve relevant memories for a query.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "project": {"type": "string"},
                "session_id": {"type": "string"},
                "limit": {"type": "integer"},
                "token_budget": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_search",
        "description": "Semantic search across all memory layers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "layer": {"type": "string"},
                "project": {"type": "string"},
                "min_score": {"type": "number"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_record_outcome",
        "description": "Record the outcome of a task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "result": {"type": "string"},
                "lesson": {"type": "string"},
                "project": {"type": "string"},
                "session_id": {"type": "string"},
            },
            "required": ["content", "result"],
        },
    },
    {
        "name": "skill_list",
        "description": "List available skills.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "status": {"type": "string"},
            },
        },
    },
    {
        "name": "approval_request",
        "description": "Create an approval request for an improvement.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "improvement_id": {"type": "string"},
            },
            "required": ["improvement_id"],
        },
    },
    {
        "name": "approval_status",
        "description": "List pending and recent approvals.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter: pending, approved, rejected, all"},
            },
        },
    },
    {
        "name": "reflection_log",
        "description": "Log a reflection with observations and lessons.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "observations": {"type": "array", "items": {"type": "string"}},
                "lessons": {"type": "array", "items": {"type": "string"}},
                "project": {"type": "string"},
            },
            "required": ["observations", "lessons"],
        },
    },
    {
        "name": "improvement_propose",
        "description": "Propose a system improvement.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "improvement_type": {"type": "string"},
                "title": {"type": "string"},
                "reason": {"type": "string"},
                "current_behavior": {"type": "string"},
                "proposed_behavior": {"type": "string"},
                "expected_benefit": {"type": "string"},
                "project": {"type": "string"},
            },
            "required": [
                "improvement_type", "title", "reason",
                "current_behavior", "proposed_behavior", "expected_benefit",
            ],
        },
    },
    {
        "name": "project_bootstrap",
        "description": (
            "Ingest a curated project capsule into Mimir for an existing repo. "
            "The caller reads the repo files and passes content as named sections; "
            "Mimir stores them as typed, trust-scored memories scoped to the project. "
            "Idempotent — aborts if bootstrap memories already exist unless force=true. "
            "Safe: never stores secrets or raw source trees — caller is responsible for "
            "passing only curated content (docs, status, governance files)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name used to scope all written memories.",
                },
                "repo_path": {
                    "type": "string",
                    "description": "Source repo path — stored as metadata only, never read by the server.",
                },
                "force": {
                    "type": "boolean",
                    "description": "Overwrite existing bootstrap memories for this project (default false).",
                },
                "profile": {
                    "type": "string",
                    "description": "Project identity: README, project_goal, pyproject.toml (combined).",
                },
                "architecture": {
                    "type": "string",
                    "description": "Repo structure: repo_map, project_knowledge, top-level docs/.",
                },
                "status": {
                    "type": "string",
                    "description": "Current state: project_status (capped), history tail, memory index.",
                },
                "constraints": {
                    "type": "string",
                    "description": "Safety + governance: AGENTS.md, CLAUDE.md, .cursor/rules/ (combined).",
                },
                "testing": {
                    "type": "string",
                    "description": "Test protocol: commands, suites, isolation rules extracted from governance docs.",
                },
                "knowledge": {
                    "type": "string",
                    "description": "Lessons learned: project_knowledge, recent history tail.",
                },
            },
            "required": ["project"],
        },
    },
]

_TOOL_NAMES = {t["name"] for t in _TOOLS}

# Dotted legacy aliases → canonical underscore names (not advertised)
_DOTTED_ALIASES: dict[str, str] = {
    "memory.remember": "memory_remember",
    "memory.recall": "memory_recall",
    "memory.search": "memory_search",
    "memory.record_outcome": "memory_record_outcome",
    "skill.list": "skill_list",
    "approval.request": "approval_request",
    "approval.status": "approval_status",
    "reflection.log": "reflection_log",
    "improvement.propose": "improvement_propose",
    "project.bootstrap": "project_bootstrap",
}


async def _run_memory_recall_tool(
    session,
    *,
    query: str,
    project: str | None,
    session_id: str | None,
    user_id: str | None,
    limit: int,
    min_score: float,
    token_budget: int | None,
) -> dict[str, Any]:
    from context.context_builder import build as build_context
    from retrieval.bootstrap_capsules import lookup_bootstrap_capsules
    from retrieval.retrieval_engine import search as retrieval_search

    hits = await retrieval_search(
        session,
        query=query,
        project=project,
        session_id=session_id,
        user_id=user_id,
        limit=limit,
        min_score=min_score,
    )
    _, bootstrap_debug = await lookup_bootstrap_capsules(
        session,
        project=project,
        query=query,
        user_id=user_id,
        limit=limit,
    )
    if bootstrap_debug["fallback_used"]:
        logger.info("mcp memory_recall bootstrap_debug=%s", bootstrap_debug)
    if token_budget:
        ctx = await build_context(
            session,
            query=query,
            project=project,
            session_id=session_id,
            token_budget=token_budget,
            user_id=user_id,
        )
        ctx["hits"] = hits
        if bootstrap_debug["fallback_used"]:
            ctx.update(bootstrap_debug)
        return ctx
    payload = {"hits": hits}
    if bootstrap_debug["fallback_used"]:
        payload.update(bootstrap_debug)
    return payload


async def _run_memory_search_tool(
    session,
    *,
    query: str,
    layer: str | None,
    project: str | None,
    user_id: str | None,
    limit: int,
    min_score: float,
) -> dict[str, Any]:
    from retrieval.bootstrap_capsules import lookup_bootstrap_capsules
    from retrieval.retrieval_engine import search as retrieval_search

    hits = await retrieval_search(
        session,
        query=query,
        layer=layer,
        project=project,
        user_id=user_id,
        limit=limit,
        min_score=min_score,
    )
    _, bootstrap_debug = await lookup_bootstrap_capsules(
        session,
        project=project,
        query=query,
        user_id=user_id,
        limit=limit,
    )
    if bootstrap_debug["fallback_used"]:
        logger.info("mcp memory_search bootstrap_debug=%s", bootstrap_debug)
    payload = {"memories": hits}
    if bootstrap_debug["fallback_used"]:
        payload.update(bootstrap_debug)
    return payload


# ── Auth ──────────────────────────────────────────────────────────────────────

def _www_authenticate_header(request: Request | None = None) -> str:
    """Build WWW-Authenticate header pointing to OAuth discovery metadata."""
    settings = get_settings()
    base = settings.public_url.rstrip("/") if settings.public_url else ""
    if not base and request:
        base = f"{request.url.scheme}://{request.url.netloc}"
    if base:
        return f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource"'
    return "Bearer"


async def _resolve_api_key(authorization: str, x_api_key: str, request: Request | None = None) -> str:
    """Extract and validate the API key or OAuth token; return key for downstream use.

    Accepts Authorization: Bearer <key|oauth_token> or X-API-Key: <key>.
    In dev-auth mode all requests are accepted (returns configured key).
    Returns the raw key string (used for user context resolution in _call_tool).
    """
    settings = get_settings()

    key = ""
    if authorization.startswith("Bearer "):
        key = authorization[7:].strip()
    if not key:
        key = x_api_key.strip()

    # Dev mode: skip auth unless an explicit Bearer token was provided
    if settings.is_dev_auth and not key:
        return settings.api_key or ""

    if not key:
        www_auth = _www_authenticate_header(request)
        raise HTTPException(
            status_code=401,
            detail="Authorization: Bearer <key> required",
            headers={"WWW-Authenticate": www_auth},
        )

    # Try OAuth access token first (always validated, even in dev mode)
    from api.routes.oauth import resolve_oauth_token, is_revoked_oauth_token
    oauth_uid = await resolve_oauth_token(key)
    if oauth_uid:
        return key

    # If the token was an OAuth token but is revoked/expired → 401 (don't fall through to API key)
    if await is_revoked_oauth_token(key):
        raise HTTPException(
            status_code=401,
            detail="Token has been revoked or expired",
            headers={"WWW-Authenticate": _www_authenticate_header(request)},
        )

    # Dev mode with explicit non-OAuth key: accept anything (for dev/test convenience)
    if settings.is_dev_auth:
        return key

    # multi_user mode: reject the default dev key (local-dev-key)
    if settings.is_multi_user and key == settings.dev_api_key and settings.dev_api_key in ("local-dev-key",):
        raise HTTPException(
            status_code=401,
            detail="Dev key not accepted in multi_user mode",
            headers={"WWW-Authenticate": _www_authenticate_header(request)},
        )

    # Fast path: matches configured MIMIR_API_KEY
    if key == settings.api_key:
        return key

    # DB-backed API key lookup
    from storage.models import APIKey
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    factory = get_session_factory()
    async with factory() as session:
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        result = await session.execute(
            select(APIKey)
            .where(APIKey.key_hash == key_hash, APIKey.is_active == True)  # noqa: E712
            .options(selectinload(APIKey.user))
        )
        row = result.scalar_one_or_none()
        if not row:
            raise HTTPException(
                status_code=401,
                detail="Invalid API key",
                headers={"WWW-Authenticate": _www_authenticate_header(request)},
            )
        if not row.user.is_active:
            raise HTTPException(status_code=403, detail="User account is inactive")

    return key


# ── Tool dispatch ─────────────────────────────────────────────────────────────

async def _call_tool(name: str, args: dict, api_key: str) -> Any:
    """Execute a tool by calling the Mimir service layer directly."""
    from sqlalchemy import select
    from api.deps import UserContext, DEV_USER_ID

    settings = get_settings()
    factory = get_session_factory()

    # Try OAuth token first (takes priority even in dev mode)
    from api.routes.oauth import resolve_oauth_token
    from storage.models import User as UserModel

    oauth_uid = await resolve_oauth_token(api_key)
    if oauth_uid:
        async with factory() as _s:
            u = await _s.get(UserModel, oauth_uid)
        if u:
            user = UserContext(id=u.id, email=u.email, display_name=u.display_name, is_dev=False)
        else:
            raise ValueError("OAuth token user not found")
    elif settings.is_dev_auth:
        user = UserContext(id=DEV_USER_ID, email="dev@local", display_name="Dev User", is_dev=True)
    elif api_key == settings.api_key and not settings.is_multi_user:
        user = UserContext(id="admin", email="admin@local", display_name="Admin", is_dev=False)
    else:
        from storage.models import APIKey
        from sqlalchemy.orm import selectinload

        async with factory() as session:
            key_hash = hashlib.sha256(api_key.encode()).hexdigest()
            r = await session.execute(
                select(APIKey)
                .where(APIKey.key_hash == key_hash, APIKey.is_active == True)  # noqa: E712
                .options(selectinload(APIKey.user))
            )
            row = r.scalar_one_or_none()
            if not row:
                raise ValueError("Invalid API key")
            user = UserContext(
                id=row.user.id,
                email=row.user.email,
                display_name=row.user.display_name,
                is_dev=False,
            )

    async with factory() as session:
        # Accept both underscore (canonical) and dotted (legacy) names
        name = _DOTTED_ALIASES.get(name, name)
        match name:
            case "memory_remember":
                from memory.memory_extractor import extract_from_event
                from memory import episodic_store, semantic_store, procedural_store

                uid = user.id
                candidates = extract_from_event(args)
                stored = []
                for c in candidates:
                    ti = c.get("trust_info") or {}
                    trust_kwargs = dict(
                        source_type=ti.get("source_type"),
                        verification_status=ti.get("verification_status"),
                        trust_score=ti.get("trust_score"),
                        confidence=ti.get("confidence"),
                        created_by=uid,
                    )
                    if c["layer"] == "episodic":
                        mem = await episodic_store.store(
                            session, c["content"],
                            project=args.get("project"), session_id=args.get("session_id"),
                            user_id=uid, importance=c["importance"], meta=c.get("meta"),
                            **trust_kwargs,
                        )
                    elif c["layer"] == "semantic":
                        mem = await semantic_store.store(
                            session, c["content"],
                            project=args.get("project"), user_id=uid,
                            importance=c["importance"], meta=c.get("meta"),
                            **trust_kwargs,
                        )
                    elif c["layer"] == "procedural":
                        mem = await procedural_store.store(
                            session, c["content"],
                            project=args.get("project"),
                            importance=c["importance"], meta=c.get("meta"),
                            **trust_kwargs,
                        )
                    else:
                        continue
                    stored.append({"id": mem.id, "layer": mem.layer})
                return {"ok": True, "stored": stored}

            case "memory_recall":
                uid = user.id if not user.is_dev else None
                return await _run_memory_recall_tool(
                    session,
                    query=args["query"],
                    project=args.get("project"),
                    session_id=args.get("session_id"),
                    user_id=uid,
                    limit=args.get("limit", 10),
                    min_score=args.get("min_score", 0.3),
                    token_budget=args.get("token_budget"),
                )

            case "memory_search":
                uid = user.id if not user.is_dev else None
                return await _run_memory_search_tool(
                    session,
                    query=args["query"],
                    layer=args.get("layer"),
                    project=args.get("project"),
                    user_id=uid,
                    limit=args.get("limit", 20),
                    min_score=args.get("min_score", 0.3),
                )

            case "memory_record_outcome":
                from memory.memory_extractor import extract_from_event
                from memory import episodic_store, semantic_store, procedural_store

                payload = {"type": "outcome", **args}
                uid = user.id
                candidates = extract_from_event(payload)
                stored = []
                for c in candidates:
                    ti = c.get("trust_info") or {}
                    trust_kwargs = dict(
                        source_type=ti.get("source_type"),
                        verification_status=ti.get("verification_status"),
                        trust_score=ti.get("trust_score"),
                        confidence=ti.get("confidence"),
                        created_by=uid,
                    )
                    if c["layer"] == "episodic":
                        mem = await episodic_store.store(
                            session, c["content"],
                            project=args.get("project"), session_id=args.get("session_id"),
                            user_id=uid, importance=c["importance"], meta=c.get("meta"),
                            **trust_kwargs,
                        )
                        stored.append({"id": mem.id, "layer": mem.layer})
                return {"ok": True, "stored": stored}

            case "skill_list":
                from storage.models import Skill

                q = select(Skill)
                if not user.is_dev:
                    q = q.where(Skill.user_id == user.id)
                if args.get("project"):
                    q = q.where(Skill.project == args["project"])
                if args.get("status"):
                    q = q.where(Skill.status == args["status"])
                result = await session.execute(q)
                skills = result.scalars().all()
                return {"skills": [
                    {"id": s.id, "name": s.name, "purpose": s.purpose, "status": s.status}
                    for s in skills
                ]}

            case "approval_request":
                from storage.models import ImprovementProposal
                from reflections.improvement_planner import create_approval_request

                imp = await session.get(ImprovementProposal, args["improvement_id"])
                if not imp:
                    raise ValueError("Improvement not found")
                if not user.is_dev and imp.user_id and imp.user_id != user.id:
                    raise ValueError("Improvement not found")
                approval = await create_approval_request(session, imp)
                approval.user_id = user.id if not user.is_dev else None
                return {"approval": {"id": approval.id, "title": approval.title, "status": approval.status}}

            case "approval_status":
                from storage.models import ApprovalRequest

                q = select(ApprovalRequest)
                if not user.is_dev:
                    q = q.where(ApprovalRequest.user_id == user.id)
                status_filter = args.get("status", "pending")
                if status_filter and status_filter != "all":
                    q = q.where(ApprovalRequest.status == status_filter)
                q = q.order_by(ApprovalRequest.created_at.desc()).limit(50)
                result = await session.execute(q)
                approvals = result.scalars().all()
                return {"approvals": [
                    {"id": a.id, "title": a.title, "status": a.status}
                    for a in approvals
                ]}

            case "reflection_log":
                from reflections import reflection_engine

                ref = await reflection_engine.log_reflection(
                    session, trigger="manual",
                    observations=args["observations"], lessons=args["lessons"],
                    project=args.get("project"),
                    user_id=user.id if not user.is_dev else None,
                )
                return {"id": ref.id, "trigger": ref.trigger, "created_at": str(ref.created_at)}

            case "improvement_propose":
                from reflections import improvement_planner

                imp = await improvement_planner.propose(
                    session,
                    improvement_type=args["improvement_type"],
                    title=args["title"],
                    reason=args["reason"],
                    current_behavior=args["current_behavior"],
                    proposed_behavior=args["proposed_behavior"],
                    expected_benefit=args["expected_benefit"],
                    project=args.get("project"),
                    user_id=user.id if not user.is_dev else None,
                )
                return {"id": imp.id, "title": imp.title, "status": imp.status}

            case "project_bootstrap":
                from memory import episodic_store, semantic_store, procedural_store
                from storage.models import Memory as _Memory
                from storage.search_backend import get_search_backend
                from storage import vector_store

                project = args.get("project")
                if not project:
                    raise ValueError("project is required")

                repo_path = args.get("repo_path", "")
                force = bool(args.get("force", False))
                run_id = datetime.now(UTC).strftime("%m%d%y_%H%M")
                uid = user.id

                def _capsule_type(meta: dict | None) -> str | None:
                    if not isinstance(meta, dict):
                        return None
                    return meta.get("capsule_type") or meta.get("bootstrap_type")

                def _capsule_heading(capsule_type: str) -> str:
                    return capsule_type.upper()

                def _label_content(capsule_type: str, body: str) -> str:
                    heading = _capsule_heading(capsule_type)
                    normalized = body.strip()
                    if normalized.startswith(f"{heading}:"):
                        return normalized
                    return (
                        f"{heading}: {project}\n"
                        f"CAPSULE_TYPE: {capsule_type}\n"
                        f"PROJECT: {project}\n\n"
                        f"{normalized}"
                    )

                def _boot_meta(capsule_type: str) -> dict:
                    return {
                        "bootstrap": True,
                        "capsule_type": capsule_type,
                        "bootstrap_type": capsule_type,  # backward compat
                        "bootstrap_run_id": f"bootstrap_{run_id}",
                        "repo_path": repo_path,
                        "project": project,
                        "project_id": project,
                    }

                # Governance rules are always generated server-side
                _GOVERNANCE = (
                    f"GOVERNANCE PRIORITY ORDER: {project}\n\n"
                    "Priority (highest to lowest):\n"
                    "1. .cursor/rules/*.md / *.mdc\n"
                    "2. AGENTS.md\n"
                    "3. CLAUDE.md\n"
                    "4. project_status.md / project_goal.md (repo truth)\n"
                    "5. Mimir recalled memories (supplemental, not authoritative)\n\n"
                    "MIMIR USAGE RULES:\n"
                    "- memory_recall: supplemental context and lessons only\n"
                    "- memory_remember: log outcomes, bugs, lessons at session end\n"
                    "- Do not store full source files, secrets, or raw logs\n"
                    "- Bootstrap memories (bootstrap=true in meta) are reference points\n"
                    "- Rerun project_bootstrap with force=true after major project changes"
                )

                # Section → (layer, importance, bootstrap_type)
                _SECTIONS = [
                    ("profile",      args.get("profile"),      "semantic",   0.95, "project_profile"),
                    ("architecture", args.get("architecture"), "semantic",   0.90, "architecture_summary"),
                    ("status",       args.get("status"),       "episodic",   0.85, "active_status"),
                    ("constraints",  args.get("constraints"),  "semantic",   0.95, "safety_constraint"),
                    ("testing",      args.get("testing"),      "procedural", 0.85, "testing_protocol"),
                    ("knowledge",    args.get("knowledge"),    "procedural", 0.80, "procedural_lesson"),
                    ("_governance",  _GOVERNANCE,              "semantic",   0.90, "governance_rules"),
                ]

                existing_rows = await session.execute(
                    select(_Memory).where(
                        _Memory.project == project,
                        _Memory.deleted_at.is_(None),
                        _Memory.user_id == uid,
                    ).limit(400)
                )
                bootstrap_existing = [
                    m for m in existing_rows.scalars()
                    if isinstance(m.meta, dict) and m.meta.get("bootstrap")
                ]
                if bootstrap_existing and not force:
                    existing_types = sorted({
                        t for t in (_capsule_type(m.meta) for m in bootstrap_existing) if t
                    })
                    return {
                        "ok": False,
                        "error": (
                            f"{len(bootstrap_existing)} bootstrap memories already exist for project "
                            f"'{project}'. Pass force=true to overwrite/reindex."
                        ),
                        "existing_count": len(bootstrap_existing),
                        "existing_capsule_types": existing_types,
                    }

                by_type: dict[str, list[Any]] = {}
                for mem in bootstrap_existing:
                    t = _capsule_type(mem.meta)
                    if not t:
                        continue
                    by_type.setdefault(t, []).append(mem)

                # If duplicates exist for one capsule type, keep the newest active row and
                # archive the rest so force=true acts as a repair path.
                for capsule_type, rows in by_type.items():
                    if len(rows) <= 1:
                        continue
                    rows.sort(key=lambda m: (m.created_at or datetime.min), reverse=True)
                    keeper = rows[0]
                    for dup in rows[1:]:
                        dup.memory_state = "archived"
                        dup.deleted_at = datetime.now(UTC)
                        dup.valid_to = datetime.now(UTC)
                        dup.superseded_by = keeper.id
                        vector_store.delete(dup.layer, dup.id)
                        session.add(dup)
                    by_type[capsule_type] = [keeper]
                await session.commit()

                stored = []
                skipped = []
                expected_types_for_run: set[str] = set()
                _trust_kwargs = dict(
                    source_type="project_bootstrap",
                    verification_status="trusted_system_observed",
                    trust_score=0.85,
                    confidence=0.85,
                    created_by=uid,
                )

                for _key, content, layer, importance, btype in _SECTIONS:
                    if not content:
                        skipped.append(btype)
                        continue
                    expected_types_for_run.add(btype)
                    content = _label_content(btype, content)
                    meta = _boot_meta(btype)
                    existing = (by_type.get(btype) or [None])[0]
                    if existing and force:
                        existing.project = project
                        existing.user_id = uid
                        existing.meta = meta
                        existing.source_type = _trust_kwargs["source_type"]
                        existing.memory_state = "active"
                        existing.verification_status = _trust_kwargs["verification_status"]
                        existing.trust_score = max(existing.trust_score or 0.0, _trust_kwargs["trust_score"])
                        existing.importance = max(existing.importance or 0.0, importance)
                        existing.deleted_at = None
                        existing.valid_to = None
                        existing.superseded_by = None
                        session.add(existing)
                        await session.commit()
                        if layer == "procedural":
                            mem = await procedural_store.update(session, existing.id, content)
                        elif layer == "semantic":
                            mem = await semantic_store.update_content(session, existing.id, content)
                        else:
                            mem = await episodic_store.update_content(session, existing.id, content)
                        if not mem:
                            raise ValueError(f"Failed to update bootstrap memory for {btype}")
                    else:
                        if layer == "episodic":
                            mem = await episodic_store.store(
                                session, content, project=project,
                                user_id=uid, importance=importance, meta=meta,
                                **_trust_kwargs,
                            )
                        elif layer == "semantic":
                            mem = await semantic_store.store(
                                session, content, project=project,
                                user_id=uid, importance=importance, meta=meta,
                                check_duplicates=False,
                                detect_conflicts=False,
                                **_trust_kwargs,
                            )
                        else:
                            mem = await procedural_store.store(
                                session, content, project=project,
                                user_id=uid,
                                importance=importance, meta=meta,
                                **_trust_kwargs,
                            )
                    stored.append({
                        "id": mem.id,
                        "layer": mem.layer,
                        "type": btype,
                        "project": mem.project,
                    })

                # Force mode acts as repair/reindex: rebuild keyword index and ensure vectors exist.
                reindexed_rows = 0
                if force:
                    backend = get_search_backend()
                    reindexed_rows = await backend.reindex(session)
                    for item in stored:
                        mem = await session.get(_Memory, item["id"])
                        if mem is None:
                            continue
                        upsert_kwargs = dict(
                            user_id=mem.user_id,
                            project_id=mem.project,
                            importance=mem.importance,
                            trust_score=mem.trust_score,
                            verification_status=mem.verification_status,
                            memory_state=mem.memory_state,
                            source_type=mem.source_type,
                            metadata=mem.meta,
                        )
                        if mem.created_at:
                            upsert_kwargs["created_at"] = mem.created_at.isoformat()
                        if mem.layer == "episodic":
                            upsert_kwargs["metadata"] = {**(mem.meta or {}), "session_id": mem.session_id or ""}
                        vector_store.upsert(mem.layer, mem.id, mem.content, **upsert_kwargs)

                # Read-after-write verification
                def _has_capsule(hits: list[dict[str, Any]], capsule_type: str) -> bool:
                    for hit in hits:
                        meta = hit.get("meta") or {}
                        t = hit.get("capsule_type")
                        if not t and isinstance(meta, dict):
                            t = meta.get("capsule_type") or meta.get("bootstrap_type")
                        if t == capsule_type:
                            return True
                    return False

                verify_checks = [
                    ("project_profile", "project_profile"),
                    ("architecture_summary", "architecture_summary"),
                    ("testing_protocol", "testing_protocol"),
                ]
                verification: dict[str, bool] = {}
                missing_capsules: list[str] = []
                for query, expected in verify_checks:
                    if expected not in expected_types_for_run:
                        continue
                    hits = (await _run_memory_search_tool(
                        session,
                        query=query,
                        layer=None,
                        project=project,
                        user_id=uid if not user.is_dev else None,
                        limit=10,
                        min_score=0.0,
                    ))["memories"]
                    ok = _has_capsule(hits, expected)
                    verification[f"search:{query}"] = ok
                    if not ok:
                        missing_capsules.append(expected)

                recall_hits = (
                    await _run_memory_recall_tool(
                        session,
                        query="what is this project",
                        project=project,
                        session_id=None,
                        user_id=uid if not user.is_dev else None,
                        limit=10,
                        min_score=0.0,
                        token_budget=None,
                    )
                )["hits"] if "project_profile" in expected_types_for_run else []
                if "project_profile" in expected_types_for_run:
                    recall_ok = _has_capsule(recall_hits, "project_profile")
                    verification["recall:what is this project"] = recall_ok
                    if not recall_ok and "project_profile" not in missing_capsules:
                        missing_capsules.append("project_profile")

                stored_by_type = {item["type"]: item["id"] for item in stored}
                missing_storage = [t for t in sorted(expected_types_for_run) if t not in stored_by_type]
                ok = not missing_capsules and not missing_storage

                return {
                    "ok": ok,
                    "warning": (
                        f"Bootstrap verification failed; missing capsule retrieval for: {sorted(set(missing_capsules))}"
                        if not ok else None
                    ),
                    "project": project,
                    "run_id": f"bootstrap_{run_id}",
                    "stored": stored,
                    "skipped": skipped,
                    "total": len(stored),
                    "project_profile_id": stored_by_type.get("project_profile"),
                    "architecture_summary_id": stored_by_type.get("architecture_summary"),
                    "active_status_id": stored_by_type.get("active_status"),
                    "safety_constraint_id": stored_by_type.get("safety_constraint"),
                    "testing_protocol_id": stored_by_type.get("testing_protocol"),
                    "procedural_lesson_id": stored_by_type.get("procedural_lesson"),
                    "governance_rules_id": stored_by_type.get("governance_rules"),
                    "verification": verification,
                    "missing_capsule_types": sorted(set(missing_capsules + missing_storage)),
                    "reindexed_rows": reindexed_rows,
                }

            case _:
                raise ValueError(f"Unknown tool: {name}")


# ── JSON-RPC helpers ──────────────────────────────────────────────────────────

def _ok(result: Any, req_id: Any) -> dict:
    return {"jsonrpc": "2.0", "result": result, "id": req_id}


def _err(code: int, message: str, req_id: Any) -> dict:
    return {"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": req_id}


async def _handle_request(msg: dict, api_key: str) -> dict:
    """Process one JSON-RPC request (msg with id); always returns a response."""
    method = msg.get("method", "")
    params = msg.get("params") or {}
    req_id = msg.get("id")

    try:
        match method:
            case "initialize":
                result = {
                    "protocolVersion": _MCP_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "mimir", "version": "1.0"},
                }
                return _ok(result, req_id)

            case "ping":
                return _ok({}, req_id)

            case "tools/list":
                return _ok({"tools": _TOOLS}, req_id)

            case "tools/call":
                tool_name = params.get("name", "")
                tool_args = params.get("arguments") or {}
                if tool_name not in _TOOL_NAMES and tool_name not in _DOTTED_ALIASES:
                    return _err(-32601, f"Unknown tool: {tool_name}", req_id)
                data = await _call_tool(tool_name, tool_args, api_key)
                return _ok(
                    {"content": [{"type": "text", "text": json.dumps(data, default=str)}]},
                    req_id,
                )

            case _:
                return _err(-32601, f"Method not found: {method}", req_id)

    except HTTPException as exc:
        return _err(-32001, exc.detail, req_id)
    except Exception as exc:
        return _err(-32000, str(exc), req_id)


def _is_notification(msg: dict) -> bool:
    """JSON-RPC notification: has method but no id."""
    return "method" in msg and "id" not in msg


def _sse_event(data: str) -> str:
    """Format one SSE event containing a JSON-RPC message."""
    return f"event: message\ndata: {data}\n\n"


async def _sse_response(responses: list[dict]) -> StreamingResponse:
    """Wrap JSON-RPC responses in a text/event-stream SSE response."""
    payload = responses[0] if len(responses) == 1 else responses
    body = json.dumps(payload, default=str)

    async def _gen():
        yield _sse_event(body)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── HTTP endpoints ────────────────────────────────────────────────────────────

@router.post("/mcp")
async def mcp_post(
    request: Request,
    authorization: str = Header(default=""),
    x_api_key: str = Header(default=""),
    accept: str = Header(default=""),
) -> Response:
    """MCP Streamable HTTP POST — client-to-server JSON-RPC messages.

    Accepts Authorization: Bearer <key|oauth_token> or X-API-Key: <key>.
    Returns text/event-stream SSE when client sends Accept: text/event-stream,
    otherwise application/json.
    Notifications (no id) → 202 Accepted per spec.
    """
    api_key = await _resolve_api_key(authorization, x_api_key, request)

    try:
        body = await request.json()
    except Exception:
        err = _err(-32700, "Parse error: invalid JSON", None)
        return _json_rpc_response(err, accept)

    messages = body if isinstance(body, list) else [body]

    # Process all messages; collect responses for requests only
    responses: list[dict] = []
    for msg in messages:
        if _is_notification(msg):
            # Notifications are fire-and-forget; ignore any errors
            pass
        else:
            resp = await _handle_request(msg, api_key)
            responses.append(resp)

    # Spec §6.4.1: all-notification batches → 202 Accepted
    if not responses:
        return Response(status_code=202)

    # Return SSE format when client accepts it (Cursor requires this)
    if "text/event-stream" in accept:
        return await _sse_response(responses)

    payload = responses[0] if len(responses) == 1 else responses
    return Response(
        content=json.dumps(payload, default=str),
        media_type="application/json",
    )


def _json_rpc_response(data: Any, accept: str) -> Response:
    body = json.dumps(data, default=str)
    if "text/event-stream" in accept:
        async def _gen():
            yield _sse_event(body)
        return StreamingResponse(
            _gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )
    return Response(content=body, media_type="application/json")


@router.get("/mcp")
async def mcp_get(
    request: Request,
    authorization: str = Header(default=""),
    x_api_key: str = Header(default=""),
) -> StreamingResponse:
    """MCP Streamable HTTP GET — server-to-client SSE channel.

    Required by the 2025-03-26 spec for server-initiated messages.
    Mimir does not push server events, but we keep the channel open with
    periodic keepalive comments so Cursor stays connected.
    """
    await _resolve_api_key(authorization, x_api_key, request)

    async def _keepalive() -> AsyncIterator[str]:
        # Initial comment — some clients wait for the first byte
        yield ": connected\n\n"
        while True:
            await asyncio.sleep(15)
            yield ": keepalive\n\n"

    return StreamingResponse(
        _keepalive(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx buffering for SSE
        },
    )


@router.delete("/mcp")
async def mcp_delete(
    request: Request,
    authorization: str = Header(default=""),
    x_api_key: str = Header(default=""),
) -> Response:
    """MCP session teardown — stateless so always succeeds."""
    await _resolve_api_key(authorization, x_api_key, request)
    return Response(status_code=200)
