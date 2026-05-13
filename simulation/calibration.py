"""Forecast calibration: track and correct prediction accuracy over time.

Reads completed simulation runs that have actual_outcome recorded,
computes accuracy, overconfidence, and underconfidence rates,
and persists a ForecastCalibration snapshot.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def compute_calibration(
    session: AsyncSession,
    project: str | None = None,
    period: str = "daily",
    lookback_days: int = 30,
) -> dict:
    """Compute forecast calibration metrics from completed simulation runs.

    Returns a dict with calibration stats and persists a ForecastCalibration row.
    """
    from storage.models import SimulationRun, ForecastCalibration

    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=lookback_days)
    q = (
        select(SimulationRun)
        .where(
            SimulationRun.actual_outcome.isnot(None),
            SimulationRun.forecast_was_correct.isnot(None),
            SimulationRun.created_at >= cutoff,
        )
    )
    if project is not None:
        q = q.where(SimulationRun.project == project)

    result = await session.execute(q)
    runs = list(result.scalars().all())

    total = len(runs)
    if total == 0:
        return {
            "total_forecasts": 0,
            "correct_forecasts": 0,
            "forecast_accuracy": 0.0,
            "overconfidence_rate": 0.0,
            "underconfidence_rate": 0.0,
            "mean_prediction_error": 0.0,
            "project": project,
            "period": period,
        }

    correct = sum(1 for r in runs if r.forecast_was_correct)
    accuracy = correct / total

    # Overconfidence: predicted high success (>0.7) but was wrong
    overconfident = [
        r for r in runs
        if (r.success_probability or 0) > 0.7
        and r.forecast_was_correct is False
    ]
    overconfidence_rate = len(overconfident) / total

    # Underconfidence: predicted low success (<0.4) but was correct
    underconfident = [
        r for r in runs
        if (r.success_probability or 0) < 0.4
        and r.forecast_was_correct is True
    ]
    underconfidence_rate = len(underconfident) / total

    # Mean prediction error: |predicted - actual_binary|
    errors: list[float] = []
    for r in runs:
        actual_binary = 1.0 if r.actual_outcome == "success" else 0.0
        predicted = r.success_probability or 0.5
        errors.append(abs(predicted - actual_binary))
    mean_error = sum(errors) / len(errors) if errors else 0.0

    cal = ForecastCalibration(
        id=str(uuid.uuid4()),
        project=project,
        period=period,
        total_forecasts=total,
        correct_forecasts=correct,
        forecast_accuracy=round(accuracy, 4),
        overconfidence_rate=round(overconfidence_rate, 4),
        underconfidence_rate=round(underconfidence_rate, 4),
        mean_prediction_error=round(mean_error, 4),
    )
    session.add(cal)
    await session.flush()

    return {
        "total_forecasts": total,
        "correct_forecasts": correct,
        "forecast_accuracy": round(accuracy, 4),
        "overconfidence_rate": round(overconfidence_rate, 4),
        "underconfidence_rate": round(underconfidence_rate, 4),
        "mean_prediction_error": round(mean_error, 4),
        "project": project,
        "period": period,
    }


async def get_calibration_history(
    session: AsyncSession,
    project: str | None = None,
    limit: int = 30,
) -> list[dict]:
    from storage.models import ForecastCalibration
    q = (
        select(ForecastCalibration)
        .order_by(ForecastCalibration.computed_at.desc())
        .limit(limit)
    )
    if project is not None:
        q = q.where(ForecastCalibration.project == project)
    result = await session.execute(q)
    rows = result.scalars().all()
    return [
        {
            "id": r.id,
            "project": r.project,
            "period": r.period,
            "total_forecasts": r.total_forecasts,
            "correct_forecasts": r.correct_forecasts,
            "forecast_accuracy": r.forecast_accuracy,
            "overconfidence_rate": r.overconfidence_rate,
            "underconfidence_rate": r.underconfidence_rate,
            "mean_prediction_error": r.mean_prediction_error,
            "computed_at": r.computed_at.isoformat() if r.computed_at else None,
        }
        for r in rows
    ]


async def record_actual_outcome(
    session: AsyncSession,
    simulation_run_id: str,
    actual_outcome: str,
) -> bool:
    """Record the real outcome of a plan execution and compute forecast_was_correct."""
    from storage.models import SimulationRun

    result = await session.execute(
        select(SimulationRun).where(SimulationRun.id == simulation_run_id)
    )
    run = result.scalar_one_or_none()
    if run is None:
        return False

    run.actual_outcome = actual_outcome
    # Forecast is correct if success_probability > 0.5 and actual is success,
    # or success_probability <= 0.5 and actual is not success
    predicted_success = (run.success_probability or 0.5) > 0.5
    actually_succeeded = actual_outcome == "success"
    run.forecast_was_correct = predicted_success == actually_succeeded

    await session.flush()
    return True
