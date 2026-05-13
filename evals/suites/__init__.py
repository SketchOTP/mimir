"""Eval suite registry."""

from __future__ import annotations

from evals.suites.memory_quality import MemoryQualitySuite
from evals.suites.retrieval_quality import RetrievalQualitySuite
from evals.suites.trust_and_quarantine import TrustAndQuarantineSuite
from evals.suites.red_team import RedTeamSuite
from evals.suites.trajectory import TrajectorySuite
from evals.suites.worker_stability import WorkerStabilitySuite
from evals.suites.token_efficiency import TokenEfficiencySuite
from evals.suites.simulation_forecasting import SimulationForecastingSuite

ALL_SUITES = {
    "memory_quality": MemoryQualitySuite,
    "retrieval_quality": RetrievalQualitySuite,
    "trust_and_quarantine": TrustAndQuarantineSuite,
    "red_team": RedTeamSuite,
    "trajectory": TrajectorySuite,
    "worker_stability": WorkerStabilitySuite,
    "token_efficiency": TokenEfficiencySuite,
    "simulation_forecasting": SimulationForecastingSuite,
}
