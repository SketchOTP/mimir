"""Bootstrap capsule query helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from memory.trust import MemoryState
from storage.models import Memory

_ALL_CAPSULES = (
    "project_profile",
    "architecture_summary",
    "active_status",
    "safety_constraint",
    "testing_protocol",
    "procedural_lesson",
    "governance_rules",
)
_BOOTSTRAP_LAYERS = ("semantic", "episodic", "procedural")


def capsule_type(meta: dict[str, Any] | None) -> str | None:
    if not isinstance(meta, dict):
        return None
    return meta.get("capsule_type") or meta.get("bootstrap_type")


def is_bootstrap_memory(meta: dict[str, Any] | None) -> bool:
    return isinstance(meta, dict) and meta.get("bootstrap") is True and capsule_type(meta) in _ALL_CAPSULES


def normalize_query(query: str) -> str:
    return " ".join(query.lower().replace("_", " ").strip().split())


def target_capsules(query: str) -> list[str]:
    q = normalize_query(query)
    found: list[str] = []

    def _add(name: str) -> None:
        if name not in found:
            found.append(name)

    for name in _ALL_CAPSULES:
        if name in query.lower() or name.replace("_", " ") in q:
            _add(name)

    if any(
        phrase in q
        for phrase in (
            "what is this project",
            "what is the project",
            "about this project",
            "project overview",
            "project context",
        )
    ):
        for name in _ALL_CAPSULES:
            _add(name)

    if "architecture" in q:
        _add("architecture_summary")

    if "status" in q:
        _add("active_status")

    if any(term in q for term in ("test", "testing", "validation", "verify", "protocol")):
        _add("testing_protocol")

    if "procedural lesson" in q or ("procedural" in q and "lesson" in q):
        _add("procedural_lesson")

    if any(term in q for term in ("safety", "constraint", "governance", "rule", "policy")):
        _add("safety_constraint")
        _add("governance_rules")

    return found


def capsule_query_score(meta: dict[str, Any] | None, query: str) -> float:
    kind = capsule_type(meta)
    if not kind:
        return 0.0

    q = normalize_query(query)
    if kind in query.lower() or kind.replace("_", " ") in q:
        return 1.0

    return {
        "project_profile": 0.99,
        "architecture_summary": 0.98,
        "active_status": 0.97,
        "safety_constraint": 0.96,
        "testing_protocol": 0.95,
        "procedural_lesson": 0.94,
        "governance_rules": 0.93,
    }.get(kind, 0.0) if kind in target_capsules(query) else 0.0


async def load_bootstrap_capsules(
    session: AsyncSession,
    *,
    project: str | None,
    query: str,
    user_id: str | None = None,
    limit: int = 10,
) -> list[Memory]:
    rows, _ = await lookup_bootstrap_capsules(
        session,
        project=project,
        query=query,
        user_id=user_id,
        limit=limit,
    )
    return rows


async def lookup_bootstrap_capsules(
    session: AsyncSession,
    *,
    project: str | None,
    query: str,
    user_id: str | None = None,
    limit: int = 10,
) -> tuple[list[Memory], dict[str, Any]]:
    wanted = target_capsules(query)
    debug = {
        "found_bootstrap_capsule_types": [],
        "missing_bootstrap_capsule_types": wanted.copy(),
        "user_id": user_id,
        "project": project,
        "layers_searched": list(_BOOTSTRAP_LAYERS),
        "fallback_used": False,
    }
    if not project or not wanted:
        return [], debug

    debug["fallback_used"] = True

    base = (
        select(Memory)
        .where(
            Memory.project == project,
            Memory.deleted_at.is_(None),
            Memory.memory_state == MemoryState.ACTIVE,
            Memory.source_type == "project_bootstrap",
        )
    )
    if user_id:
        base = base.where(Memory.user_id == user_id)

    rows: list[Memory] = []
    try:
        q = base.where(
            Memory.meta["bootstrap"].as_boolean().is_(True),
            or_(
                Memory.meta["capsule_type"].as_string().in_(wanted),
                Memory.meta["bootstrap_type"].as_string().in_(wanted),
            ),
        )
        result = await session.execute(q)
        rows = list(result.scalars())
    except Exception:
        result = await session.execute(base)
        rows = list(result.scalars())

    filtered = [
        mem
        for mem in rows
        if is_bootstrap_memory(mem.meta)
        and capsule_type(mem.meta) in wanted
    ]

    found = [capsule_type(mem.meta) for mem in filtered if capsule_type(mem.meta)]
    debug["found_bootstrap_capsule_types"] = found
    debug["missing_bootstrap_capsule_types"] = [name for name in wanted if name not in found]

    order = {name: i for i, name in enumerate(wanted)}
    filtered.sort(
        key=lambda mem: (
            order.get(capsule_type(mem.meta) or "", len(order)),
            -(mem.importance or 0.0),
            -(mem.trust_score or 0.0),
            -(
                mem.created_at.replace(tzinfo=None).timestamp()
                if isinstance(mem.created_at, datetime) else 0.0
            ),
        )
    )
    return filtered[:limit], debug
