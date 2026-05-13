"""Reflection and improvement endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import ReflectionLogIn, ReflectionOut, ImprovementProposeIn, ImprovementOut
from api.deps import UserContext, get_current_user
from storage.database import get_session
from reflections import reflection_engine, improvement_planner

router = APIRouter(tags=["reflections"])


@router.post("/reflections", response_model=ReflectionOut)
async def log_reflection(
    body: ReflectionLogIn,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    ref = await reflection_engine.log_reflection(
        session,
        trigger=body.trigger,
        observations=body.observations,
        lessons=body.lessons,
        proposed_improvements=body.proposed_improvements,
        project=body.project,
        session_id=body.session_id,
        user_id=current_user.id if not current_user.is_dev else None,
    )
    return ref


@router.post("/reflections/generate", response_model=ReflectionOut)
async def generate_reflection(
    project: str | None = None,
    window_hours: int = 24,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    ref = await reflection_engine.generate(session, project=project, window_hours=window_hours)
    return ref


@router.get("/reflections")
async def list_reflections(
    project: str | None = None,
    limit: int = 20,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    from storage.models import Reflection
    q = select(Reflection)
    if not current_user.is_dev:
        q = q.where(Reflection.user_id == current_user.id)
    if project:
        q = q.where(Reflection.project == project)
    q = q.order_by(Reflection.created_at.desc()).limit(limit)
    result = await session.execute(q)
    refs = result.scalars().all()
    return {"reflections": [ReflectionOut.model_validate(r) for r in refs]}


@router.post("/improvements/propose", response_model=ImprovementOut)
async def propose_improvement(
    body: ImprovementProposeIn,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    imp = await improvement_planner.propose(
        session,
        improvement_type=body.improvement_type,
        title=body.title,
        reason=body.reason,
        current_behavior=body.current_behavior,
        proposed_behavior=body.proposed_behavior,
        expected_benefit=body.expected_benefit,
        risk=body.risk,
        project=body.project,
        meta=body.meta,
        user_id=current_user.id if not current_user.is_dev else None,
    )
    return imp


@router.get("/improvements")
async def list_improvements(
    status: str | None = None,
    project: str | None = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    from storage.models import ImprovementProposal
    q = select(ImprovementProposal)
    if not current_user.is_dev:
        q = q.where(ImprovementProposal.user_id == current_user.id)
    if status:
        q = q.where(ImprovementProposal.status == status)
    if project:
        q = q.where(ImprovementProposal.project == project)
    q = q.order_by(ImprovementProposal.created_at.desc()).limit(limit)
    result = await session.execute(q)
    props = result.scalars().all()
    return {"improvements": [ImprovementOut.model_validate(p) for p in props]}


@router.get("/improvements/{improvement_id}", response_model=ImprovementOut)
async def get_improvement(
    improvement_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    from storage.models import ImprovementProposal
    imp = await session.get(ImprovementProposal, improvement_id)
    if not imp:
        raise HTTPException(404, "Improvement not found")
    if not current_user.is_dev and imp.user_id and imp.user_id != current_user.id:
        raise HTTPException(404, "Improvement not found")
    return imp
