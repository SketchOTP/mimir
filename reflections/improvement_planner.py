"""Turn reflections into structured ImprovementProposals and queue them for approval."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, UTC

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mimir.config import get_settings
from storage.models import ImprovementProposal, ApprovalRequest, Reflection

logger = logging.getLogger(__name__)

# Map of improvement types to their default risk level
_RISK_MAP = {
    "skill_refine": "low",
    "skill_update": "low",
    "memory_policy": "medium",
    "retrieval_tune": "low",
    "context_tune": "low",
    "approval_format": "low",
    "notification_tune": "low",
    "rollback": "low",
    "infrastructure": "high",
}

# Minimum time (seconds) between reflections on the same project to prevent flood
_REFLECTION_COOLDOWN_S = 3600  # 1 hour

# Similarity threshold for duplicate proposal suppression (title comparison)
_DUPLICATE_TITLE_MIN_WORDS = 3


async def propose(
    session: AsyncSession,
    improvement_type: str,
    title: str,
    reason: str,
    current_behavior: str,
    proposed_behavior: str,
    expected_benefit: str,
    reflection_id: str | None = None,
    project: str | None = None,
    risk: str | None = None,
    meta: dict | None = None,
    user_id: str | None = None,
) -> ImprovementProposal:
    imp = ImprovementProposal(
        id=f"imp_{uuid.uuid4().hex[:16]}",
        reflection_id=reflection_id,
        improvement_type=improvement_type,
        title=title,
        reason=reason,
        current_behavior=current_behavior,
        proposed_behavior=proposed_behavior,
        risk=risk or _RISK_MAP.get(improvement_type, "medium"),
        expected_benefit=expected_benefit,
        status="proposed",
        project=project,
        meta=meta,
        user_id=user_id,
    )
    session.add(imp)
    await session.commit()
    return imp


async def create_approval_request(
    session: AsyncSession,
    improvement: ImprovementProposal,
    expires_hours: int = 72,
) -> ApprovalRequest:
    approval = ApprovalRequest(
        id=f"apr_{uuid.uuid4().hex[:16]}",
        improvement_id=improvement.id,
        title=improvement.title,
        request_type=improvement.improvement_type,
        summary={
            "id": improvement.id,
            "type": improvement.improvement_type,
            "title": improvement.title,
            "reason": improvement.reason,
            "current_behavior": improvement.current_behavior,
            "proposed_behavior": improvement.proposed_behavior,
            "risk": improvement.risk,
            "expected_benefit": improvement.expected_benefit,
            "test_result": improvement.test_result,
            "actions": ["approve", "reject", "view_details"],
        },
        expires_at=datetime.now(UTC) + timedelta(hours=expires_hours),
    )
    session.add(approval)
    improvement.status = "pending_approval"
    await session.commit()
    return approval


async def plan_from_reflection(
    session: AsyncSession, reflection: Reflection
) -> list[ImprovementProposal]:
    """Convert a reflection's proposed_improvements into ImprovementProposal records."""
    proposals = []
    for item in (reflection.proposed_improvements or []):
        if item.get("type") == "skill_refine":
            imp = await propose(
                session,
                improvement_type="skill_refine",
                title="Skill refinement needed",
                reason=item.get("reason", ""),
                current_behavior="Skills failing above threshold",
                proposed_behavior="Review and refine failing skills",
                expected_benefit="Lower failure rate, more reliable automation",
                reflection_id=reflection.id,
                project=reflection.project,
            )
            proposals.append(imp)
        elif item.get("type") == "retrieval_tune":
            imp = await propose(
                session,
                improvement_type="retrieval_tune",
                title="Retrieval relevance improvement",
                reason=item.get("reason", ""),
                current_behavior="Retrieval relevance below 0.5",
                proposed_behavior="Adjust embedding thresholds and retrieval weights",
                expected_benefit="More relevant context, fewer token waste",
                reflection_id=reflection.id,
                project=reflection.project,
            )
            proposals.append(imp)
    return proposals


async def list_proposals(
    session: AsyncSession,
    status: str | None = None,
    project: str | None = None,
    limit: int = 50,
) -> list[ImprovementProposal]:
    q = select(ImprovementProposal).order_by(ImprovementProposal.created_at.desc()).limit(limit)
    if status:
        q = q.where(ImprovementProposal.status == status)
    if project:
        q = q.where(ImprovementProposal.project == project)
    result = await session.execute(q)
    return list(result.scalars())
