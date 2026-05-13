"""Memory CRUD endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import MemoryIn, MemoryOut, MemoryPatch
from api.deps import UserContext, get_current_user
from storage.database import get_session
from storage.models import Memory
from memory import episodic_store, semantic_store, procedural_store
from memory.memory_extractor import extract_trust_info

router = APIRouter(prefix="/memory", tags=["memory"])


@router.post("", response_model=MemoryOut)
async def create_memory(
    body: MemoryIn,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    uid = body.user_id or current_user.id
    kwargs = dict(project=body.project, importance=body.importance, meta=body.meta)
    trust_info = extract_trust_info(body.content)
    trust_kwargs = dict(
        source_type=trust_info.get("source_type"),
        verification_status=trust_info.get("verification_status"),
        trust_score=trust_info.get("trust_score"),
        confidence=trust_info.get("confidence"),
        created_by=uid,
    )
    if body.layer == "episodic":
        mem = await episodic_store.store(
            session, body.content, session_id=body.session_id, user_id=uid, **kwargs, **trust_kwargs
        )
    elif body.layer == "semantic":
        mem = await semantic_store.store(session, body.content, user_id=uid, **kwargs, **trust_kwargs)
    elif body.layer == "procedural":
        mem = await procedural_store.store(session, body.content, **kwargs, **trust_kwargs)
    else:
        raise HTTPException(400, f"Unknown layer: {body.layer}")
    return mem


@router.get("")
async def list_memories(
    layer: str | None = None,
    project: str | None = None,
    session_id: str | None = None,
    memory_state: str | None = None,
    verification_status: str | None = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    q = select(Memory).where(Memory.deleted_at.is_(None))
    if not current_user.is_dev:
        q = q.where(Memory.user_id == current_user.id)
    if layer:
        q = q.where(Memory.layer == layer)
    if project:
        q = q.where(Memory.project == project)
    if session_id:
        q = q.where(Memory.session_id == session_id)
    if memory_state:
        q = q.where(Memory.memory_state == memory_state)
    if verification_status:
        q = q.where(Memory.verification_status == verification_status)
    q = q.order_by(Memory.created_at.desc()).limit(limit)
    result = await session.execute(q)
    mems = result.scalars().all()
    return {"memories": [MemoryOut.model_validate(m) for m in mems]}


@router.get("/{memory_id}", response_model=MemoryOut)
async def get_memory(
    memory_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    mem = await session.get(Memory, memory_id)
    if not mem or mem.deleted_at:
        raise HTTPException(404, "Memory not found")
    if not current_user.is_dev and mem.user_id and mem.user_id != current_user.id:
        raise HTTPException(404, "Memory not found")
    return mem


@router.patch("/{memory_id}", response_model=MemoryOut)
async def update_memory(
    memory_id: str,
    patch: MemoryPatch,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    mem = await session.get(Memory, memory_id)
    if not mem or mem.deleted_at:
        raise HTTPException(404, "Memory not found")
    if not current_user.is_dev and mem.user_id and mem.user_id != current_user.id:
        raise HTTPException(404, "Memory not found")
    if patch.content is not None:
        if mem.layer == "semantic":
            await semantic_store.update_content(session, memory_id, patch.content)
        elif mem.layer == "procedural":
            await procedural_store.update(session, memory_id, patch.content)
        elif mem.layer == "episodic":
            await episodic_store.update_content(session, memory_id, patch.content)
        else:
            mem.content = patch.content
    if patch.importance is not None:
        mem.importance = patch.importance
    if patch.meta is not None:
        mem.meta = {**(mem.meta or {}), **patch.meta}
    await session.commit()
    await session.refresh(mem)
    return mem


@router.delete("/{memory_id}")
async def delete_memory(
    memory_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    mem = await session.get(Memory, memory_id)
    if not mem or mem.deleted_at:
        raise HTTPException(404, "Memory not found")
    if not current_user.is_dev and mem.user_id and mem.user_id != current_user.id:
        raise HTTPException(404, "Memory not found")
    if mem.layer == "episodic":
        ok = await episodic_store.delete(session, memory_id)
    elif mem.layer == "semantic":
        ok = await semantic_store.delete(session, memory_id)
    elif mem.layer == "procedural":
        ok = await procedural_store.delete(session, memory_id)
    else:
        ok = False
    return {"ok": ok}
