"""Base types for eval suites."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from httpx import AsyncClient


@dataclass
class EvalResult:
    suite: str
    name: str
    passed: bool
    score: float | None = None
    detail: str = ""
    critical: bool = False          # True → failure blocks release gate
    metric_name: str | None = None  # optional named metric for report
    metric_value: float | None = None


@dataclass
class EvalReport:
    timestamp: str
    suites_run: list[str]
    total: int
    passed: int
    failed: int
    critical_failures: list[str]
    results: list[dict[str, Any]]
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def gate_passed(self) -> bool:
        return len(self.critical_failures) == 0 and self.failed == 0 or (
            # allow non-critical failures as warnings
            len(self.critical_failures) == 0
        )


class EvalSuite(ABC):
    NAME: str = "unnamed"
    DESCRIPTION: str = ""

    async def run(self, client: AsyncClient) -> list[EvalResult]:
        raise NotImplementedError

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _ok(
        self,
        name: str,
        detail: str = "",
        score: float | None = None,
        metric_name: str | None = None,
        metric_value: float | None = None,
    ) -> EvalResult:
        return EvalResult(
            suite=self.NAME,
            name=name,
            passed=True,
            detail=detail,
            score=score,
            metric_name=metric_name,
            metric_value=metric_value,
        )

    def _fail(
        self,
        name: str,
        detail: str = "",
        critical: bool = False,
        score: float | None = None,
        metric_name: str | None = None,
        metric_value: float | None = None,
    ) -> EvalResult:
        return EvalResult(
            suite=self.NAME,
            name=name,
            passed=False,
            detail=detail,
            critical=critical,
            score=score,
            metric_name=metric_name,
            metric_value=metric_value,
        )

    def _gate(self, name: str, condition: bool, detail: str = "") -> EvalResult:
        """Critical check — failure blocks release."""
        return self._ok(name, detail) if condition else self._fail(name, detail, critical=True)
