"""POST /events, POST /recall, POST /recall/feedback, and retrieval session endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, UTC

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import EventIn, RecallRequest, RecallFeedbackIn, RetrievalSessionOutcomeIn
from api.deps import UserContext, get_current_user
from storage.database import get_session
from storage.models import Memory, RetrievalFeedback, LifecycleEvent, RetrievalSession
from memory.memory_extractor import extract_from_event
from memory import episodic_store, semantic_store, procedural_store
from memory.trust import MemoryState
from context.context_builder import build as build_context
from retrieval.retrieval_engine import search as retrieval_search

router = APIRouter(prefix="/events", tags=["events"])

# Trust adjustments per feedback outcome
_FEEDBACK_TRUST_DELTA = {
    "success": +0.02,
    "failure": -0.05,
    "irrelevant": -0.02,
    "harmful": -0.10,
}
_TRUST_MAX = 0.99
_TRUST_MIN = 0.01
_VALID_OUTCOMES = frozenset(_FEEDBACK_TRUST_DELTA)


@router.post("")
async def ingest_event(
    event: EventIn,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    uid = event.user_id or current_user.id
    candidates = extract_from_event(event.model_dump())
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
                project=event.project, session_id=event.session_id,
                user_id=uid, importance=c["importance"], meta=c.get("meta"),
                **trust_kwargs,
            )
        elif c["layer"] == "semantic":
            mem = await semantic_store.store(
                session, c["content"],
                project=event.project, user_id=uid,
                importance=c["importance"], meta=c.get("meta"),
                **trust_kwargs,
            )
        elif c["layer"] == "procedural":
            mem = await procedural_store.store(
                session, c["content"],
                project=event.project, importance=c["importance"], meta=c.get("meta"),
                **trust_kwargs,
            )
        else:
            continue
        stored.append({"id": mem.id, "layer": mem.layer})

    return {"ok": True, "stored": stored}


@router.post("/recall")
async def recall(
    req: RecallRequest,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    """Unified recall endpoint (P6: orchestrated multi-source retrieval).

    Always returns:
      {
        "query": str,
        "hits": [...],           # raw vector hits (always present)
        "context": {             # only present when token_budget is given
          "memories": [...],
          "token_cost": int,
          "debug": { providers, selected, excluded, agreement_scores, ... }
        },
        "debug": {               # top-level P6 debug (only with token_budget)
          "providers": [...],
          "selected": [...],
          "excluded": [...],
          "agreement_scores": {...},
          "token_cost": int
        }
      }
    """
    uid = req.user_id or (None if current_user.is_dev else current_user.id)
    hits = await retrieval_search(
        session,
        req.query,
        layer=req.layer,
        project=req.project,
        session_id=req.session_id,
        user_id=uid,
        limit=req.limit,
        min_score=req.min_score,
    )
    result: dict = {"query": req.query, "hits": hits}

    if req.token_budget is not None:
        ctx = await build_context(
            session,
            req.query,
            project=req.project,
            session_id=req.session_id,
            user_id=uid,
            token_budget=req.token_budget,
        )
        ctx_debug = ctx.get("debug", {})
        selected_memories = ctx.get("memories", [])
        token_cost = ctx.get("token_count", 0)

        result["context"] = {
            "memories": selected_memories,
            "token_cost": token_cost,
            "debug": ctx_debug,
        }
        # Top-level debug block (P6 directive requirement)
        result["debug"] = {
            "providers": ctx_debug.get("providers", []),
            "selected": ctx_debug.get("selected", []),
            "excluded": ctx_debug.get("excluded", []),
            "agreement_scores": ctx_debug.get("agreement_scores", {}),
            "token_cost": ctx_debug.get("token_cost", 0),
        }

        # P9/P10: persist retrieval session for causal attribution + telemetry
        agreement_scores = ctx_debug.get("agreement_scores", {})
        retrieved_ids = [
            m["id"] if isinstance(m, dict) else m
            for m in selected_memories
            if (isinstance(m, dict) and "id" in m) or isinstance(m, str)
        ]
        avg_agreement = (
            sum(agreement_scores.values()) / len(agreement_scores)
            if agreement_scores else None
        )
        token_efficiency: float | None = None
        if req.token_budget and token_cost:
            ratio = token_cost / req.token_budget
            token_efficiency = max(0.0, 1.0 - abs(1.0 - min(ratio, 2.0)))

        # P10: provider contributions — count memories per source
        provider_contributions: dict[str, int] = {}
        for item in ctx_debug.get("selected", []):
            for src in item.get("provider_sources", []):
                provider_contributions[src] = provider_contributions.get(src, 0) + 1

        rs = RetrievalSession(
            id=uuid.uuid4().hex,
            query=req.query,
            session_id=req.session_id,
            project=req.project,
            user_id=uid,
            retrieved_memory_ids=retrieved_ids,
            result_count=len(retrieved_ids),
            token_cost=token_cost,
            agreement_score=round(avg_agreement, 4) if avg_agreement is not None else None,
            token_efficiency_score=round(token_efficiency, 4) if token_efficiency is not None else None,
            # P10 fields
            task_category=ctx_debug.get("task_category"),
            active_providers=ctx_debug.get("providers"),
            provider_contributions=provider_contributions or None,
            retrieval_confidence_score=ctx_debug.get("retrieval_confidence"),
        )
        session.add(rs)
        try:
            await session.commit()
        except Exception:
            await session.rollback()
        result["retrieval_session_id"] = rs.id
        result["retrieval_confidence"] = ctx_debug.get("retrieval_confidence", 0.0)
        result["task_category"] = ctx_debug.get("task_category", "general")

    return result


@router.post("/recall/feedback")
async def recall_feedback(
    req: RecallFeedbackIn,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    """Record retrieval outcome to drive trust evolution.

    Successful retrievals increase trust; failures, irrelevance, and harmful
    outcomes decrease it.  Procedural memories also get last_success_at /
    last_failure_at updated so the promoter can track evidence quality.
    """
    if req.outcome not in _VALID_OUTCOMES:
        raise HTTPException(
            status_code=422,
            detail=f"outcome must be one of: {sorted(_VALID_OUTCOMES)}",
        )

    mem = await session.get(Memory, req.memory_id)
    if not mem or mem.deleted_at:
        raise HTTPException(status_code=404, detail="Memory not found")
    if mem.memory_state in MemoryState.BLOCKED:
        raise HTTPException(status_code=409, detail="Cannot record feedback for blocked memory")

    uid = current_user.id if not current_user.is_dev else None
    now = datetime.now(UTC)

    # Persist raw feedback record
    fb = RetrievalFeedback(
        id=uuid.uuid4().hex,
        memory_id=req.memory_id,
        outcome=req.outcome,
        reason=req.reason,
        user_id=uid,
    )
    session.add(fb)

    # Apply trust delta
    delta = _FEEDBACK_TRUST_DELTA[req.outcome]
    old_trust = mem.trust_score or 0.7
    new_trust = max(_TRUST_MIN, min(_TRUST_MAX, old_trust + delta))
    mem.trust_score = new_trust

    # Update retrieval counters
    is_success = req.outcome == "success"
    if is_success:
        mem.successful_retrievals = (mem.successful_retrievals or 0) + 1
        mem.last_success_at = now
        mem.last_verified_at = now
    else:
        mem.failed_retrievals = (mem.failed_retrievals or 0) + 1
        mem.last_failure_at = now

    session.add(mem)

    # Lifecycle event for audit
    event_type = "trust_increased" if delta > 0 else "trust_decreased"
    session.add(LifecycleEvent(
        id=uuid.uuid4().hex,
        memory_id=req.memory_id,
        event_type=event_type,
        from_state=mem.memory_state,
        to_state=mem.memory_state,
        trust_before=old_trust,
        trust_after=new_trust,
        reason=f"retrieval_feedback:{req.outcome}",
        meta={"reason": req.reason, "feedback_id": fb.id},
    ))

    await session.commit()

    return {
        "ok": True,
        "memory_id": req.memory_id,
        "outcome": req.outcome,
        "trust_before": round(old_trust, 4),
        "trust_after": round(new_trust, 4),
    }


_VALID_TASK_OUTCOMES = frozenset({"success", "failure", "partial"})


@router.post("/recall/session/{retrieval_session_id}/outcome")
async def record_session_outcome(
    retrieval_session_id: str,
    req: RetrievalSessionOutcomeIn,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    """Record the task outcome for a retrieval session.

    This enables the automatic feedback inference engine to infer trust signals
    for all memories that were retrieved in that session.
    """
    if req.task_outcome not in _VALID_TASK_OUTCOMES:
        raise HTTPException(
            status_code=422,
            detail=f"task_outcome must be one of: {sorted(_VALID_TASK_OUTCOMES)}",
        )

    rs = await session.get(RetrievalSession, retrieval_session_id)
    if not rs:
        raise HTTPException(status_code=404, detail="Retrieval session not found")

    rs.task_outcome = req.task_outcome
    rs.has_correction = req.has_correction
    rs.has_harmful_outcome = req.has_harmful_outcome
    if req.rollback_id:
        rs.rollback_id = req.rollback_id

    # Compute quality scores now that outcome is known
    from telemetry.retrieval_analytics import compute_session_quality_scores
    scores = compute_session_quality_scores(
        rs.retrieved_memory_ids or [],
        rs.token_cost or 0,
        4096,  # default budget estimate
        {m: rs.agreement_score or 0.0 for m in (rs.retrieved_memory_ids or [])},
        task_outcome=req.task_outcome,
        has_harmful_outcome=req.has_harmful_outcome,
    )
    rs.relevance_score = scores["relevance_score"]
    rs.usefulness_score = scores["usefulness_score"]
    rs.harmfulness_score = scores["harmfulness_score"]

    session.add(rs)
    await session.commit()

    return {
        "ok": True,
        "retrieval_session_id": retrieval_session_id,
        "task_outcome": req.task_outcome,
        "quality_scores": scores,
    }
