"""Bootstrap capsule query helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from memory.trust import MemoryState
from storage.models import Memory

_BLOCKED = list(MemoryState.BLOCKED)
_ALL_CAPSULES = (
    "project_profile",
    "architecture_summary",
    "active_status",
    "safety_constraint",
    "testing_protocol",
    "procedural_lesson",
    "governance_rules",
)


def capsule_type(meta: dict[str, Any] | None) -> str | None:
    if not isinstance(meta, dict):
        return None
    return meta.get("capsule_type") or meta.get("bootstrap_type")


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
    if not project:
        return []

    wanted = target_capsules(query)
    if not wanted:
        return []

    base = (
        select(Memory)
        .where(
            Memory.project == project,
            Memory.deleted_at.is_(None),
            Memory.memory_state.notin_(_BLOCKED),
            Memory.source_type == "project_bootstrap",
        )
    )
    if user_id:
        base = base.where(or_(Memory.user_id == user_id, Memory.user_id.is_(None)))

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
        if isinstance(mem.meta, dict)
        and mem.meta.get("bootstrap") is True
        and capsule_type(mem.meta) in wanted
    ]

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
    return filtered[:limit]
