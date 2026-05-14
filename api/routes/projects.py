"""Project memory profile API — per-project memory counts and bootstrap health."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import UserContext, get_current_user
from storage.database import get_session
from storage.models import Memory

router = APIRouter(prefix="/api/projects", tags=["projects"])

_BOOTSTRAP_CAPSULE_TYPES = {
    "project_profile",
    "architecture_summary",
    "active_status",
    "safety_constraint",
    "governance_rules",
    "testing_protocol",
    "procedural_lesson",
}


async def _project_summary(session: AsyncSession, project: str, user_id: str) -> dict:
    # Memory counts by layer
    layer_q = await session.execute(
        select(Memory.layer, func.count(Memory.id))
        .where(
            Memory.project == project,
            Memory.user_id == user_id,
            Memory.deleted_at.is_(None),
        )
        .group_by(Memory.layer)
    )
    counts_by_layer = dict(layer_q.fetchall())
    total = sum(counts_by_layer.values())

    # Bootstrap capsule presence
    bootstrap_q = await session.execute(
        select(Memory.id, Memory.meta, Memory.created_at)
        .where(
            Memory.project == project,
            Memory.user_id == user_id,
            Memory.source_type == "project_bootstrap",
            Memory.deleted_at.is_(None),
        )
        .order_by(Memory.created_at.desc())
    )
    bootstrap_rows = bootstrap_q.fetchall()
    present_types: set[str] = set()
    last_bootstrap_at: str | None = None
    for row in bootstrap_rows:
        meta = row.meta or {}
        capsule_type = meta.get("capsule_type") or meta.get("bootstrap_capsule_type")
        if capsule_type:
            present_types.add(capsule_type)
        if last_bootstrap_at is None and row.created_at:
            ts = row.created_at
            last_bootstrap_at = (
                ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            )

    missing_types = _BOOTSTRAP_CAPSULE_TYPES - present_types
    bootstrap_health = "healthy" if not missing_types else ("partial" if present_types else "missing")

    return {
        "project": project,
        "memory_count": total,
        "counts_by_layer": counts_by_layer,
        "bootstrap": {
            "health": bootstrap_health,
            "present_capsule_types": sorted(present_types),
            "missing_capsule_types": sorted(missing_types),
            "capsule_count": len(bootstrap_rows),
            "last_bootstrap_at": last_bootstrap_at,
        },
    }


@router.get("")
async def list_projects(
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    """List all projects for the current user with memory counts and bootstrap status."""
    user_id = str(current_user.id)
    result = await session.execute(
        select(Memory.project)
        .where(
            Memory.user_id == user_id,
            Memory.project.is_not(None),
            Memory.deleted_at.is_(None),
        )
        .group_by(Memory.project)
        .order_by(Memory.project)
    )
    slugs = [row[0] for row in result.fetchall() if row[0]]
    projects = [await _project_summary(session, slug, user_id) for slug in slugs]
    return {"projects": projects, "count": len(projects)}


@router.get("/{slug}")
async def get_project(
    slug: str,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    """Get memory profile and bootstrap health for a single project."""
    user_id = str(current_user.id)
    return await _project_summary(session, slug, user_id)
