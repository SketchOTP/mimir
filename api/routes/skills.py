"""Skill management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import SkillProposeIn, SkillOut, SkillRunIn, SkillResultIn
from api.deps import UserContext, get_current_user
from storage.database import get_session
from skills import skill_registry, skill_runner, skill_tester

router = APIRouter(prefix="/skills", tags=["skills"])


@router.post("/propose", response_model=SkillOut)
async def propose_skill(
    body: SkillProposeIn,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    skill = await skill_registry.create(
        session,
        body.name,
        body.purpose,
        trigger_conditions=body.trigger_conditions,
        steps=body.steps,
        tools_required=body.tools_required,
        permissions_required=body.permissions_required,
        test_cases=body.test_cases,
        project=body.project,
        meta=body.meta,
        user_id=current_user.id if not current_user.is_dev else None,
    )
    return skill


@router.get("")
async def list_skills(
    project: str | None = None,
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    from storage.models import Skill
    q = select(Skill)
    if not current_user.is_dev:
        q = q.where(Skill.user_id == current_user.id)
    if project:
        q = q.where(Skill.project == project)
    if status:
        q = q.where(Skill.status == status)
    result = await session.execute(q)
    skills = result.scalars().all()
    return {"skills": [SkillOut.model_validate(s) for s in skills]}


@router.get("/{skill_id}", response_model=SkillOut)
async def get_skill(
    skill_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    skill = await skill_registry.get(session, skill_id)
    if not skill:
        raise HTTPException(404, "Skill not found")
    if not current_user.is_dev and skill.user_id and skill.user_id != current_user.id:
        raise HTTPException(404, "Skill not found")
    return skill


@router.post("/{skill_id}/run")
async def run_skill(
    skill_id: str,
    body: SkillRunIn,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    skill = await skill_registry.get(session, skill_id)
    if skill and not current_user.is_dev and skill.user_id and skill.user_id != current_user.id:
        raise HTTPException(404, "Skill not found")
    result = await skill_runner.run(session, skill_id, input_data=body.input_data)
    return result


@router.post("/{skill_id}/result")
async def record_skill_result(
    skill_id: str,
    body: SkillResultIn,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    ok = await skill_runner.record_result(session, skill_id, body.run_id, body.outcome, body.output_data)
    return {"ok": ok}


@router.post("/{skill_id}/test")
async def test_skill(
    skill_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    result = await skill_tester.test_skill(session, skill_id)
    return result
