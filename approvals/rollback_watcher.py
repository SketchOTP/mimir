"""Automatically rollback promoted improvements that degrade metrics."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, UTC

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import ImprovementProposal, SkillVersion, Rollback, MetricRecord, ApprovalRequest
from skills import skill_registry

logger = logging.getLogger(__name__)

# Degradation thresholds for each metric (how much it may worsen before rollback)
_METRIC_THRESHOLDS: dict[str, float] = {
    "skill_success_rate": 0.15,         # drop of >15% triggers rollback
    "retrieval_relevance_score": 0.10,  # drop of >0.10 triggers rollback
    "context_token_cost": -500,         # increase of >500 tokens triggers rollback (negative = bad growth)
    "user_correction_rate": -0.10,      # increase of >10% triggers rollback
    "approval_reject_rate": -0.15,      # increase of >15% triggers rollback
    "memory_precision": 0.10,           # drop of >10% triggers rollback
}

# Metrics where a higher value is worse (so a positive delta = degradation)
_HIGHER_IS_WORSE = {"context_token_cost", "user_correction_rate", "approval_reject_rate"}

# Minimum observation window before we evaluate a skill for rollback
MIN_OBSERVATIONS = 10


async def watch_and_rollback(session: AsyncSession) -> list[str]:
    """Check all promoted improvements and rollback if any key metric degraded."""
    rolled_back: list[str] = []

    try:
        await _watch_skill_improvements(session, rolled_back)
    except Exception as exc:
        await session.rollback()
        logger.error("_watch_skill_improvements failed: %s", exc)

    try:
        await _watch_system_metrics(session, rolled_back)
    except Exception as exc:
        await session.rollback()
        logger.error("_watch_system_metrics failed: %s", exc)

    return rolled_back


async def _watch_skill_improvements(session: AsyncSession, rolled_back: list[str]) -> None:
    q = select(ImprovementProposal).where(
        ImprovementProposal.status == "promoted",
        ImprovementProposal.improvement_type == "skill_update",
    )
    result = await session.execute(q)

    for imp in result.scalars():
        if not imp.meta:
            continue
        skill_id = imp.meta.get("skill_id")
        if not skill_id:
            continue

        skill = await skill_registry.get(session, skill_id)
        if not skill:
            continue

        total = skill.success_count + skill.failure_count
        if total < MIN_OBSERVATIONS:
            continue

        current_rate = skill.success_count / total

        version_q = select(SkillVersion).where(
            SkillVersion.skill_id == skill_id
        ).order_by(SkillVersion.version.asc()).limit(1)
        version_result = await session.execute(version_q)
        baseline_ver = version_result.scalars().first()

        if not baseline_ver or not baseline_ver.metrics_before:
            continue

        baseline_rate = baseline_ver.metrics_before.get("success_rate", 0.5)
        threshold = _METRIC_THRESHOLDS["skill_success_rate"]
        if (baseline_rate - current_rate) > threshold:
            await _do_skill_rollback(
                session, imp, skill_id, skill.version - 1,
                metric="skill_success_rate",
                baseline=baseline_rate,
                current=current_rate,
            )
            rolled_back.append(imp.id)


async def _watch_system_metrics(session: AsyncSession, rolled_back: list[str]) -> None:
    """
    For non-skill promotions, check if any key system metric has degraded since the
    improvement was promoted.
    """
    q = select(ImprovementProposal).where(
        ImprovementProposal.status == "promoted",
        ImprovementProposal.improvement_type.notin_(["skill_update"]),
    )
    result = await session.execute(q)
    improvements = list(result.scalars())
    if not improvements:
        return

    for imp in improvements:
        if imp.id in rolled_back:
            continue
        promoted_at_str = (imp.meta or {}).get("promoted_at")
        if not promoted_at_str:
            continue
        try:
            promoted_at = datetime.fromisoformat(promoted_at_str)
        except ValueError:
            continue

        # Compare "before" metrics (recorded up to 24h before promotion) to "after"
        before_window = promoted_at - timedelta(hours=24)
        after_window = promoted_at

        for metric_name, threshold in _METRIC_THRESHOLDS.items():
            if metric_name == "skill_success_rate":
                continue  # handled above

            before_q = await session.execute(
                select(func.avg(MetricRecord.value)).where(
                    MetricRecord.name == metric_name,
                    MetricRecord.recorded_at >= before_window,
                    MetricRecord.recorded_at < after_window,
                )
            )
            before_val = before_q.scalar()

            after_q = await session.execute(
                select(func.avg(MetricRecord.value)).where(
                    MetricRecord.name == metric_name,
                    MetricRecord.recorded_at >= after_window,
                )
            )
            after_val = after_q.scalar()

            if before_val is None or after_val is None:
                continue

            if metric_name in _HIGHER_IS_WORSE:
                degradation = after_val - before_val  # positive = got worse
                bad = degradation > abs(threshold)
            else:
                degradation = before_val - after_val  # positive = got worse
                bad = degradation > threshold

            if bad:
                rollback = Rollback(
                    id=f"rb_{uuid.uuid4().hex[:16]}",
                    target_type="policy",
                    target_id=imp.id,
                    from_version=None,
                    to_version=None,
                    metrics_before={metric_name: float(before_val)},
                    metrics_after={metric_name: float(after_val)},
                    reason=(
                        f"Auto-rollback: {metric_name} degraded "
                        f"from {before_val:.3f} to {after_val:.3f}"
                    ),
                    automatic=True,
                )
                session.add(rollback)
                imp.status = "rolled_back"
                await session.commit()
                logger.warning(
                    "Auto-rolled back improvement %s (%s degraded %.3f → %.3f)",
                    imp.id, metric_name, before_val, after_val,
                )
                rolled_back.append(imp.id)
                break  # one metric sufficient to trigger rollback


async def _do_skill_rollback(
    session: AsyncSession,
    imp: ImprovementProposal,
    skill_id: str,
    to_version: int,
    *,
    metric: str,
    baseline: float,
    current: float,
) -> None:
    skill = await skill_registry.get(session, skill_id)
    if not skill:
        return

    version_q = select(SkillVersion).where(
        SkillVersion.skill_id == skill_id,
        SkillVersion.version == to_version,
    )
    version_result = await session.execute(version_q)
    snap = version_result.scalars().first()

    if snap and snap.snapshot:
        allowed_keys = {
            "name", "purpose", "trigger_conditions", "steps",
            "tools_required", "permissions_required",
        }
        updates = {k: v for k, v in snap.snapshot.items() if k in allowed_keys}
        await skill_registry.update(session, skill_id, updates)

    await skill_registry.set_status(session, skill_id, "rolled_back")
    imp.status = "rolled_back"

    rollback = Rollback(
        id=f"rb_{uuid.uuid4().hex[:16]}",
        target_type="skill",
        target_id=skill_id,
        from_version=skill.version,
        to_version=to_version,
        metrics_before={metric: baseline},
        metrics_after={metric: current},
        reason=f"Auto-rollback: {metric} degraded from {baseline:.3f} to {current:.3f}",
        automatic=True,
    )
    session.add(rollback)
    await session.commit()
    logger.warning(
        "Auto-rolled back skill %s (%s: %.3f → %.3f)",
        skill_id, metric, baseline, current,
    )
