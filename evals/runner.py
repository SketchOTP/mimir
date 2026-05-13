"""Eval runner — load suites, run them, produce JSON + Markdown reports.

Usage:
    python -m evals.runner --suite all --out reports/evals/latest.json
    python -m evals.runner --suite memory_quality,red_team --out /tmp/eval.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, UTC
from pathlib import Path

# Set eval-specific DB so we don't corrupt the test DB.
_EVAL_DATA_DIR = os.environ.get("MIMIR_EVAL_DATA_DIR", "/tmp/mimir_eval")
os.environ.setdefault("MIMIR_DATA_DIR", _EVAL_DATA_DIR)
os.environ.setdefault("MIMIR_VECTOR_DIR", f"{_EVAL_DATA_DIR}/vectors")
os.environ.setdefault("MIMIR_ENV", "development")
os.environ.setdefault("MIMIR_ENABLE_SYSTEM_MUTATION_ENDPOINTS", "true")

from httpx import AsyncClient, ASGITransport  # noqa: E402

from evals.base import EvalReport, EvalResult, EvalSuite  # noqa: E402
from evals.suites import ALL_SUITES  # noqa: E402

logger = logging.getLogger(__name__)

_REPORT_DIR = Path(__file__).parent / "reports"


# ─── Report generation ────────────────────────────────────────────────────────

def _to_dict(r: EvalResult) -> dict:
    return {
        "suite": r.suite,
        "name": r.name,
        "passed": r.passed,
        "score": r.score,
        "detail": r.detail,
        "critical": r.critical,
        "metric_name": r.metric_name,
        "metric_value": r.metric_value,
    }


def build_report(
    suites_run: list[str],
    all_results: list[EvalResult],
    started_at: str,
) -> EvalReport:
    total = len(all_results)
    passed = sum(1 for r in all_results if r.passed)
    failed = total - passed
    critical_failures = [
        f"{r.suite}.{r.name}: {r.detail}"
        for r in all_results
        if not r.passed and r.critical
    ]
    metrics: dict[str, float] = {}
    for r in all_results:
        if r.metric_name is not None and r.metric_value is not None:
            # Last writer wins (most specific check wins for each metric)
            metrics[r.metric_name] = r.metric_value

    return EvalReport(
        timestamp=started_at,
        suites_run=suites_run,
        total=total,
        passed=passed,
        failed=failed,
        critical_failures=critical_failures,
        results=[_to_dict(r) for r in all_results],
        metrics=metrics,
    )


def write_json(report: EvalReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(
            {
                "timestamp": report.timestamp,
                "suites_run": report.suites_run,
                "total": report.total,
                "passed": report.passed,
                "failed": report.failed,
                "critical_failures": report.critical_failures,
                "gate_passed": report.gate_passed,
                "metrics": report.metrics,
                "results": report.results,
            },
            f,
            indent=2,
        )


def write_markdown(report: EvalReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# Mimir Eval Report")
    lines.append(f"\n**Date:** {report.timestamp[:19].replace('T', ' ')} UTC")
    lines.append(f"**Suites:** {', '.join(report.suites_run)}\n")
    lines.append("## Summary\n")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total checks | {report.total} |")
    lines.append(f"| Passed | {report.passed} |")
    lines.append(f"| Failed | {report.failed} |")
    lines.append(f"| Critical failures | {len(report.critical_failures)} |")
    lines.append(f"| Release gate | {'**PASS**' if report.gate_passed else '**FAIL**'} |\n")

    if report.critical_failures:
        lines.append("## Critical Failures\n")
        for cf in report.critical_failures:
            lines.append(f"- {cf}")
        lines.append("")

    if report.metrics:
        lines.append("## Metrics\n")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        for k, v in sorted(report.metrics.items()):
            lines.append(f"| {k} | {v:.4f} |")
        lines.append("")

    lines.append("## Results by Suite\n")
    by_suite: dict[str, list[dict]] = {}
    for r in report.results:
        by_suite.setdefault(r["suite"], []).append(r)

    for suite_name, suite_results in by_suite.items():
        n_pass = sum(1 for r in suite_results if r["passed"])
        n_total = len(suite_results)
        lines.append(f"### {suite_name} ({n_pass}/{n_total})\n")
        lines.append("| Check | Status | Detail |")
        lines.append("|-------|--------|--------|")
        for r in suite_results:
            status = "PASS" if r["passed"] else ("FAIL ⛔" if r["critical"] else "FAIL")
            detail = r["detail"][:80].replace("|", "\\|") if r["detail"] else ""
            lines.append(f"| {r['name']} | {status} | {detail} |")
        lines.append("")

    with open(path, "w") as f:
        f.write("\n".join(lines))


# ─── Runner ───────────────────────────────────────────────────────────────────

async def run_suites(
    suite_names: list[str],
    verbose: bool = False,
) -> tuple[list[str], list[EvalResult]]:
    """Run the requested suites and return (suites_run, all_results)."""
    if "all" in suite_names:
        suite_names = list(ALL_SUITES.keys())

    unknown = [s for s in suite_names if s not in ALL_SUITES]
    if unknown:
        raise ValueError(f"Unknown suites: {unknown}. Available: {list(ALL_SUITES.keys())}")

    # Initialise the eval DB
    import shutil
    from storage.database import init_db
    from api.main import app

    eval_db = f"{_EVAL_DATA_DIR}/mimir.db"
    if os.path.exists(eval_db):
        os.remove(eval_db)
    eval_vecs = f"{_EVAL_DATA_DIR}/vectors"
    if os.path.exists(eval_vecs):
        shutil.rmtree(eval_vecs)

    await init_db()

    all_results: list[EvalResult] = []

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://eval") as client:
        for suite_name in suite_names:
            suite: EvalSuite = ALL_SUITES[suite_name]()
            if verbose:
                print(f"\n  Running suite: {suite_name} …")
            try:
                results = await suite.run(client)
            except Exception as exc:
                logger.exception("Suite %s raised an exception", suite_name)
                results = [EvalResult(
                    suite=suite_name,
                    name="suite_error",
                    passed=False,
                    critical=True,
                    detail=f"Unhandled exception: {exc}",
                )]

            all_results.extend(results)

            if verbose:
                passed = sum(1 for r in results if r.passed)
                print(f"    {passed}/{len(results)} passed")
                for r in results:
                    if not r.passed:
                        crit = " [CRITICAL]" if r.critical else ""
                        print(f"    FAIL{crit}: {r.name} — {r.detail}")

    return suite_names, all_results


async def _main(args: argparse.Namespace) -> int:
    suite_names = [s.strip() for s in args.suite.split(",")]
    started_at = datetime.now(UTC).isoformat()

    print(f"Mimir Eval Runner — {started_at[:19]} UTC")
    print(f"Suites: {args.suite}")

    suites_run, all_results = await run_suites(suite_names, verbose=True)
    report = build_report(suites_run, all_results, started_at)

    out_path = Path(args.out)
    write_json(report, out_path)
    md_path = out_path.with_suffix(".md")
    write_markdown(report, md_path)

    print(f"\n{'='*60}")
    print(f"Total: {report.total}  Passed: {report.passed}  Failed: {report.failed}")
    print(f"Critical failures: {len(report.critical_failures)}")
    print(f"Release gate: {'PASS' if report.gate_passed else 'FAIL'}")
    print(f"Report: {out_path}")
    print(f"Summary: {md_path}")

    if report.critical_failures:
        print("\nCritical failures:")
        for cf in report.critical_failures:
            print(f"  ⛔  {cf}")

    return 0 if report.gate_passed else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Mimir eval runner")
    parser.add_argument(
        "--suite",
        default="all",
        help="Comma-separated suite names or 'all' (default: all)",
    )
    parser.add_argument(
        "--out",
        default=str(_REPORT_DIR / "latest.json"),
        help="Output path for JSON report",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
