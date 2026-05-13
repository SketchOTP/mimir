"""Background task implementations with job locking, timeout, and structured failure logging."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, UTC
from functools import wraps
from typing import Any, Callable

from storage.database import get_session_factory

logger = logging.getLogger(__name__)

# In-process lock: prevents concurrent calls within a single worker process
_running_jobs: set[str] = set()

# Job timeout in seconds
_JOB_TIMEOUT_S = 300


def _job(job_id: str, timeout: int = _JOB_TIMEOUT_S, db_lock: bool = False):
    """Decorator adding in-process lock, optional DB lock, timeout, and error logging.

    Set db_lock=True for jobs that must not run concurrently across workers
    (consolidation, lifecycle, graph build, etc.).
    """
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        async def wrapper(*args, **kwargs) -> Any:
            # In-process guard (fast path — no DB needed)
            if job_id in _running_jobs:
                logger.warning(
                    "job_skipped",
                    extra={"job_id": job_id, "reason": "already_running_in_process"},
                )
                return None

            # DB-backed distributed lock (cross-worker guard)
            if db_lock:
                try:
                    from worker.job_lock import try_acquire, release
                    factory = get_session_factory()
                    async with factory() as lock_session:
                        acquired = await try_acquire(lock_session, job_id, ttl=timeout + 60)
                        await lock_session.commit()
                        if not acquired:
                            logger.info(
                                "job_skipped",
                                extra={"job_id": job_id, "reason": "db_lock_held_by_peer"},
                            )
                            return None
                except Exception as exc:
                    logger.debug("DB lock unavailable for %s (%s) — continuing", job_id, exc)

            _running_jobs.add(job_id)
            started_at = time.monotonic()
            try:
                result = await asyncio.wait_for(fn(*args, **kwargs), timeout=timeout)
                duration_ms = int((time.monotonic() - started_at) * 1000)
                logger.info(
                    "job_completed",
                    extra={"job_id": job_id, "duration_ms": duration_ms, "status": "ok"},
                )
                return result
            except asyncio.TimeoutError:
                duration_ms = int((time.monotonic() - started_at) * 1000)
                logger.error(
                    "job_timeout",
                    extra={
                        "event_type": "worker_failure",
                        "job_id": job_id,
                        "duration_ms": duration_ms,
                        "status": "timeout",
                        "error": f"Job exceeded {timeout}s timeout",
                    },
                )
            except Exception as exc:
                duration_ms = int((time.monotonic() - started_at) * 1000)
                logger.error(
                    "job_failed",
                    extra={
                        "event_type": "worker_failure",
                        "job_id": job_id,
                        "duration_ms": duration_ms,
                        "status": "error",
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                    exc_info=True,
                )
            finally:
                _running_jobs.discard(job_id)
                if db_lock:
                    try:
                        from worker.job_lock import release
                        factory = get_session_factory()
                        async with factory() as lock_session:
                            await release(lock_session, job_id)
                            await lock_session.commit()
                    except Exception:
                        pass
            return None
        return wrapper
    return decorator


def get_running_jobs() -> list[str]:
    return sorted(_running_jobs)


@_job("reflection_cycle", timeout=_JOB_TIMEOUT_S)
async def run_reflection_cycle(project: str | None = None) -> None:
    """Generate a reflection and convert it into improvement proposals."""
    from reflections.reflection_engine import generate
    from reflections.improvement_planner import plan_from_reflection

    factory = get_session_factory()
    async with factory() as session:
        ref = await generate(session, project=project)
        if ref is None:
            logger.debug("Reflection cycle: skipped — no actionable signals")
            return
        proposals = await plan_from_reflection(session, ref)
        logger.info("Reflection cycle: %d proposals from %s", len(proposals), ref.id)


@_job("skill_analysis", timeout=_JOB_TIMEOUT_S)
async def run_skill_analysis(project: str | None = None) -> None:
    """Auto-detect repeating task patterns and propose skills."""
    from skills.skill_generator import analyze_and_propose

    factory = get_session_factory()
    async with factory() as session:
        new_skills = await analyze_and_propose(session, project=project)
        logger.info("Skill analysis: %d new skills proposed", len(new_skills))


@_job("promotion_cycle", timeout=120)
async def run_promotion_cycle() -> None:
    """Promote all approved improvements."""
    from approvals.promotion_worker import promote_approved

    factory = get_session_factory()
    async with factory() as session:
        promoted = await promote_approved(session)
        logger.info("Promotion cycle: %d improvements promoted", len(promoted))


@_job("rollback_watch", timeout=120)
async def run_rollback_watch() -> None:
    """Check promoted improvements for degradation and auto-rollback if needed."""
    from approvals.rollback_watcher import watch_and_rollback

    factory = get_session_factory()
    async with factory() as session:
        rolled_back = await watch_and_rollback(session)
        if rolled_back:
            logger.warning("Rollback: %d improvements rolled back", len(rolled_back))


@_job("consolidation", timeout=_JOB_TIMEOUT_S)
async def run_consolidation() -> None:
    """Prune stale memories and deduplicate semantic store."""
    from memory.memory_consolidator import prune_stale, deduplicate_semantic

    factory = get_session_factory()
    async with factory() as session:
        pruned = await prune_stale(session)
        deduped = await deduplicate_semantic(session)
        logger.info("Consolidation: pruned=%d, deduped=%d", pruned, deduped)


@_job("metrics_snapshot", timeout=120)
async def run_metrics_snapshot() -> None:
    """Compute and persist daily metrics."""
    from metrics.metrics_engine import compute_and_record_all

    factory = get_session_factory()
    async with factory() as session:
        metrics = await compute_and_record_all(session)
        logger.info("Metrics snapshot: %s", metrics)


@_job("expire_approvals", timeout=60)
async def run_expire_approvals() -> None:
    from approvals.approval_queue import expire_stale

    factory = get_session_factory()
    async with factory() as session:
        n = await expire_stale(session)
        if n:
            logger.info("Expired %d stale approvals", n)


@_job("reflection_pass", timeout=_JOB_TIMEOUT_S, db_lock=True)
async def run_reflection_pass(project: str | None = None) -> None:
    """Detect contradictions, extract patterns, and propose improvements (offline)."""
    from worker.reflector import run_reflection_pass

    factory = get_session_factory()
    async with factory() as session:
        result = await run_reflection_pass(session, project=project)
        logger.info(
            "Reflection pass: contradictions=%d proposals=%d",
            result["contradictions_flagged"],
            result["proposals_created"],
        )


@_job("consolidation_pass", timeout=_JOB_TIMEOUT_S, db_lock=True)
async def run_consolidation_pass(project: str | None = None) -> None:
    """Nightly dreaming: trust update + episodic chains + merge + dedup + prune."""
    from worker.consolidator import run_consolidation_pass

    factory = get_session_factory()
    async with factory() as session:
        result = await run_consolidation_pass(session, project=project)
        logger.info(
            "Consolidation pass: trust_updated=%d chains=%d merged=%d pruned=%d deduped=%d",
            result["trust_updated"],
            result["chains_built"],
            result["merged"],
            result["pruned"],
            result["deduped"],
        )


@_job("lifecycle_pass", timeout=_JOB_TIMEOUT_S, db_lock=True)
async def run_lifecycle_pass() -> None:
    """Nightly lifecycle: aging + stale + archive + verification decay."""
    from worker.lifecycle import run_lifecycle_pass

    factory = get_session_factory()
    async with factory() as session:
        result = await run_lifecycle_pass(session)
        logger.info(
            "Lifecycle pass: aged=%d staled=%d archived=%d decayed=%d",
            result["aged"],
            result["staled"],
            result["archived"],
            result["verification_decayed"],
        )


@_job("deep_maintenance", timeout=_JOB_TIMEOUT_S)
async def run_deep_maintenance() -> None:
    """Weekly deep maintenance: full lifecycle pass + hard-delete cleanup."""
    from worker.lifecycle import run_deep_maintenance

    factory = get_session_factory()
    async with factory() as session:
        result = await run_deep_maintenance(session)
        logger.info(
            "Deep maintenance: aged=%d staled=%d archived=%d decayed=%d hard_deleted=%d",
            result["aged"],
            result["staled"],
            result["archived"],
            result["verification_decayed"],
            result["hard_deleted"],
        )


@_job("telemetry_snapshot", timeout=120)
async def run_telemetry_snapshot(project: str | None = None) -> None:
    """Compute and persist cognitive telemetry metrics."""
    from telemetry.cognition_metrics import compute_snapshot

    factory = get_session_factory()
    async with factory() as session:
        metrics = await compute_snapshot(session, project=project)
        logger.info("Telemetry snapshot: %d metrics recorded", len(metrics))


@_job("drift_detection", timeout=120)
async def run_drift_detection(project: str | None = None) -> None:
    """Detect confidence drift and apply conservative trust decay."""
    from telemetry.procedural_analytics import detect_confidence_drift, apply_drift_trust_decay

    factory = get_session_factory()
    async with factory() as session:
        candidates = await detect_confidence_drift(session, project=project)
        decayed = await apply_drift_trust_decay(session, candidates)
        if candidates:
            logger.info(
                "Drift detection: %d candidates found, %d decayed",
                len(candidates), decayed
            )


@_job("graph_build", timeout=_JOB_TIMEOUT_S, db_lock=True)
async def run_graph_build(project: str | None = None) -> None:
    """Nightly graph build: extract relationships from episodic chains, rollbacks, improvements, etc."""
    from graph.graph_builder import run_graph_build_pass

    factory = get_session_factory()
    async with factory() as session:
        result = await run_graph_build_pass(session)
        logger.info(
            "Graph build: total=%d episodic=%d memory_rel=%d rollbacks=%d retrieval=%d",
            result["total"],
            result["episodic_edges"],
            result["memory_relation_edges"],
            result["rollback_edges"],
            result["retrieval_edges"],
        )


@_job("forecast_calibration", timeout=120)
async def run_forecast_calibration(project: str | None = None) -> None:
    """Compute and persist forecast calibration metrics (P12)."""
    from simulation.calibration import compute_calibration

    factory = get_session_factory()
    async with factory() as session:
        result = await compute_calibration(session, project=project)
        logger.info(
            "Forecast calibration: total=%d correct=%d accuracy=%.2f",
            result["total_forecasts"],
            result["correct_forecasts"],
            result["forecast_accuracy"],
        )


@_job("provider_stats_aggregation", timeout=120)
async def run_provider_stats_aggregation(project: str | None = None) -> None:
    """Aggregate per-provider usefulness stats from recent retrieval sessions (P10)."""
    from retrieval.provider_stats import aggregate_provider_stats

    factory = get_session_factory()
    async with factory() as session:
        result = await aggregate_provider_stats(session, project=project)
        logger.info(
            "Provider stats: sessions=%d stats_rows=%d drift_flagged=%d",
            result["sessions_processed"],
            result["stats_updated"],
            result["drift_flagged"],
        )
