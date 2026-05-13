"""Track and query Mimir system metrics."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import MetricRecord, Memory, SkillRun, RetrievalLog, ContextBuild

METRIC_NAMES = [
    "memory_precision", "memory_recall", "false_memory_rate", "duplicate_memory_rate",
    "stale_memory_rate", "retrieval_relevance_score", "context_token_cost",
    "skill_success_rate", "improvement_success_rate", "rollback_rate",
    "approval_accept_rate", "user_correction_rate",
]


async def record(
    session: AsyncSession,
    name: str,
    value: float,
    project: str | None = None,
    period: str | None = None,
    meta: dict | None = None,
) -> MetricRecord:
    rec = MetricRecord(
        id=f"met_{uuid.uuid4().hex[:16]}",
        name=name,
        value=value,
        project=project,
        period=period,
        meta=meta,
    )
    session.add(rec)
    await session.commit()
    return rec


async def get_latest(
    session: AsyncSession, name: str, project: str | None = None
) -> MetricRecord | None:
    q = select(MetricRecord).where(MetricRecord.name == name)
    if project:
        q = q.where(MetricRecord.project == project)
    q = q.order_by(MetricRecord.recorded_at.desc()).limit(1)
    result = await session.execute(q)
    return result.scalars().first()


async def get_history(
    session: AsyncSession,
    name: str,
    project: str | None = None,
    days: int = 30,
) -> list[MetricRecord]:
    since = datetime.utcnow() - timedelta(days=days)
    q = (
        select(MetricRecord)
        .where(MetricRecord.name == name, MetricRecord.recorded_at > since)
        .order_by(MetricRecord.recorded_at.asc())
    )
    if project:
        q = q.where(MetricRecord.project == project)
    result = await session.execute(q)
    return list(result.scalars())


async def compute_and_record_all(
    session: AsyncSession, project: str | None = None
) -> dict[str, float]:
    """Auto-compute key metrics from DB state and record them."""
    metrics = {}
    now = datetime.utcnow()
    day_ago = now - timedelta(hours=24)

    # Skill success rate
    runs_q = await session.execute(select(SkillRun).where(SkillRun.created_at > day_ago).limit(200))
    runs = list(runs_q.scalars())
    if runs:
        success = sum(1 for r in runs if r.outcome == "success")
        rate = success / len(runs)
        metrics["skill_success_rate"] = rate
        await record(session, "skill_success_rate", rate, project=project, period="daily")

    # Retrieval relevance (avg top score from logs)
    ret_q = await session.execute(
        select(func.avg(RetrievalLog.top_score)).where(
            RetrievalLog.created_at > day_ago,
            RetrievalLog.top_score.isnot(None),
        )
    )
    avg_ret = ret_q.scalar()
    if avg_ret is not None:
        metrics["retrieval_relevance_score"] = float(avg_ret)
        await record(session, "retrieval_relevance_score", float(avg_ret), project=project, period="daily")

    # Context token cost (avg)
    ctx_q = await session.execute(
        select(func.avg(ContextBuild.token_count)).where(ContextBuild.created_at > day_ago)
    )
    avg_ctx = ctx_q.scalar()
    if avg_ctx is not None:
        metrics["context_token_cost"] = float(avg_ctx)
        await record(session, "context_token_cost", float(avg_ctx), project=project, period="daily")

    return metrics


async def get_dashboard_metrics(session: AsyncSession) -> dict[str, Any]:
    """Aggregate metrics for the dashboard."""
    from storage.models import Skill, ApprovalRequest, Rollback, ImprovementProposal

    mem_count = await session.execute(select(func.count(Memory.id)).where(Memory.deleted_at.is_(None)))
    skill_count = await session.execute(
        select(func.count(Skill.id)).where(Skill.status == "active")
    )
    pending_approvals = await session.execute(
        select(func.count(ApprovalRequest.id)).where(ApprovalRequest.status == "pending")
    )
    rollback_count = await session.execute(select(func.count(Rollback.id)))
    imp_count = await session.execute(
        select(func.count(ImprovementProposal.id)).where(ImprovementProposal.status == "promoted")
    )

    latest_metrics = {}
    for name in ["retrieval_relevance_score", "skill_success_rate", "context_token_cost"]:
        rec = await get_latest(session, name)
        if rec:
            latest_metrics[name] = rec.value

    return {
        "memory_count": mem_count.scalar_one(),
        "skill_count": skill_count.scalar_one(),
        "pending_approvals": pending_approvals.scalar_one(),
        "rollback_events": rollback_count.scalar_one(),
        "improvements_promoted": imp_count.scalar_one(),
        **latest_metrics,
    }
