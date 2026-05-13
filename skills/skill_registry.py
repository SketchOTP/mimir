"""CRUD operations for the skill catalog."""

from __future__ import annotations

import uuid
from datetime import datetime, UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import Skill, SkillVersion


async def create(
    session: AsyncSession,
    name: str,
    purpose: str,
    *,
    trigger_conditions: list = None,
    steps: list = None,
    tools_required: list = None,
    permissions_required: list = None,
    test_cases: list = None,
    project: str | None = None,
    source_task_ids: list | None = None,
    meta: dict | None = None,
    user_id: str | None = None,
) -> Skill:
    skill = Skill(
        id=f"skill_{uuid.uuid4().hex[:16]}",
        name=name,
        purpose=purpose,
        trigger_conditions=trigger_conditions or [],
        steps=steps or [],
        tools_required=tools_required or [],
        permissions_required=permissions_required or [],
        test_cases=test_cases or [],
        project=project,
        source_task_ids=source_task_ids,
        meta=meta,
        user_id=user_id,
    )
    session.add(skill)
    await session.commit()
    await _snapshot(session, skill, "created")
    return skill


async def get(session: AsyncSession, skill_id: str) -> Skill | None:
    return await session.get(Skill, skill_id)


async def list_skills(
    session: AsyncSession,
    project: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[Skill]:
    q = select(Skill)
    if project:
        q = q.where(Skill.project == project)
    if status:
        q = q.where(Skill.status == status)
    q = q.order_by(Skill.created_at.desc()).limit(limit)
    result = await session.execute(q)
    return list(result.scalars())


async def update(session: AsyncSession, skill_id: str, updates: dict) -> Skill | None:
    skill = await get(session, skill_id)
    if not skill:
        return None
    for k, v in updates.items():
        if hasattr(skill, k):
            setattr(skill, k, v)
    skill.version += 1
    skill.updated_at = datetime.now(UTC)
    await session.commit()
    await _snapshot(session, skill, "updated")
    return skill


async def set_status(session: AsyncSession, skill_id: str, status: str) -> Skill | None:
    skill = await get(session, skill_id)
    if not skill:
        return None
    skill.status = status
    skill.updated_at = datetime.now(UTC)
    await session.commit()
    return skill


async def record_run_outcome(session: AsyncSession, skill_id: str, success: bool) -> None:
    skill = await get(session, skill_id)
    if not skill:
        return
    if success:
        skill.success_count += 1
    else:
        skill.failure_count += 1
    await session.commit()


async def get_versions(session: AsyncSession, skill_id: str) -> list[SkillVersion]:
    q = select(SkillVersion).where(SkillVersion.skill_id == skill_id).order_by(SkillVersion.version.desc())
    result = await session.execute(q)
    return list(result.scalars())


async def _snapshot(session: AsyncSession, skill: Skill, reason: str) -> None:
    snap = SkillVersion(
        id=uuid.uuid4().hex,
        skill_id=skill.id,
        version=skill.version,
        snapshot={
            "name": skill.name,
            "purpose": skill.purpose,
            "trigger_conditions": skill.trigger_conditions,
            "steps": skill.steps,
            "tools_required": skill.tools_required,
            "permissions_required": skill.permissions_required,
            "status": skill.status,
        },
        promoted_reason=reason,
    )
    session.add(snap)
    await session.commit()
