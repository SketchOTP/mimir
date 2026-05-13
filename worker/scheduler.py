"""APScheduler-based background worker."""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from worker.tasks import (
    run_reflection_cycle,
    run_skill_analysis,
    run_promotion_cycle,
    run_rollback_watch,
    run_consolidation,
    run_metrics_snapshot,
    run_expire_approvals,
    run_reflection_pass,
    run_consolidation_pass,
    run_lifecycle_pass,
    run_deep_maintenance,
    run_telemetry_snapshot,
    run_drift_detection,
    run_provider_stats_aggregation,
    run_graph_build,
    run_forecast_calibration,
)

logger = logging.getLogger(__name__)


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()

    # Reflection cycle: every 6 hours
    scheduler.add_job(run_reflection_cycle, IntervalTrigger(hours=6), id="reflection_cycle",
                      misfire_grace_time=300)

    # Skill analysis: every 12 hours
    scheduler.add_job(run_skill_analysis, IntervalTrigger(hours=12), id="skill_analysis",
                      misfire_grace_time=300)

    # Promotion check: every 5 minutes
    scheduler.add_job(run_promotion_cycle, IntervalTrigger(minutes=5), id="promotion_cycle",
                      misfire_grace_time=60)

    # Rollback watch: every 15 minutes
    scheduler.add_job(run_rollback_watch, IntervalTrigger(minutes=15), id="rollback_watch",
                      misfire_grace_time=60)

    # Memory consolidation: daily
    scheduler.add_job(run_consolidation, IntervalTrigger(hours=24), id="consolidation",
                      misfire_grace_time=3600)

    # Metrics snapshot: every hour
    scheduler.add_job(run_metrics_snapshot, IntervalTrigger(hours=1), id="metrics_snapshot",
                      misfire_grace_time=300)

    # Expire approvals: every hour
    scheduler.add_job(run_expire_approvals, IntervalTrigger(hours=1), id="expire_approvals",
                      misfire_grace_time=300)

    # Reflector pass: every 30 minutes (pattern analysis + contradiction detection)
    scheduler.add_job(run_reflection_pass, IntervalTrigger(minutes=30), id="reflection_pass",
                      misfire_grace_time=120)

    # Consolidator pass: nightly (dreaming — trust update + chains + merge + dedup)
    scheduler.add_job(run_consolidation_pass, IntervalTrigger(hours=24), id="consolidation_pass",
                      misfire_grace_time=3600)

    # Lifecycle pass: nightly (aging + stale + archive + verification decay)
    scheduler.add_job(run_lifecycle_pass, IntervalTrigger(hours=24), id="lifecycle_pass",
                      misfire_grace_time=3600)

    # Deep maintenance: weekly (lifecycle + hard-delete cleanup)
    scheduler.add_job(run_deep_maintenance, IntervalTrigger(weeks=1), id="deep_maintenance",
                      misfire_grace_time=3600)

    # Telemetry snapshot: every 6 hours (P9)
    scheduler.add_job(run_telemetry_snapshot, IntervalTrigger(hours=6), id="telemetry_snapshot",
                      misfire_grace_time=300)

    # Drift detection: daily (P9)
    scheduler.add_job(run_drift_detection, IntervalTrigger(hours=24), id="drift_detection",
                      misfire_grace_time=3600)

    # Provider stats aggregation: every 6 hours (P10)
    scheduler.add_job(run_provider_stats_aggregation, IntervalTrigger(hours=6),
                      id="provider_stats_aggregation", misfire_grace_time=300)

    # Graph build: nightly (P11)
    scheduler.add_job(run_graph_build, IntervalTrigger(hours=24), id="graph_build",
                      misfire_grace_time=3600)

    # Forecast calibration: daily (P12)
    scheduler.add_job(run_forecast_calibration, IntervalTrigger(hours=24),
                      id="forecast_calibration", misfire_grace_time=3600)

    return scheduler


async def _run_async():
    scheduler = create_scheduler()
    scheduler.start()
    logger.info("Mimir worker started")
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        logger.info("Mimir worker stopped")


def run():
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run_async())


if __name__ == "__main__":
    run()
