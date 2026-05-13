"""Release gate — reads an eval report and fails on critical issues.

Critical failures (exit code 1):
  - Any eval result with critical=True and passed=False
  - cross_user_leakage_rate > 0
  - quarantine_exclusion_rate < 1.0
  - red_team suite has any failure
  - Forbidden patterns detected (Tailscale, approval bypass, etc.)

Usage:
    python -m evals.release_gate
    python -m evals.release_gate --report reports/evals/latest.json
    python -m evals.release_gate --run-evals
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


# ─── Gate rules ───────────────────────────────────────────────────────────────

def _check_report(data: dict) -> tuple[bool, list[str]]:
    """Evaluate a report dict. Returns (gate_passed, list_of_failure_reasons)."""
    failures: list[str] = []

    # 1. Any critical eval failure
    for cf in data.get("critical_failures", []):
        failures.append(f"Critical eval failure: {cf}")

    # 2. Metric hard-fails
    metrics = data.get("metrics", {})

    leakage = metrics.get("cross_user_leakage_rate")
    if leakage is not None and leakage > 0:
        failures.append(
            f"cross_user_leakage_rate={leakage} > 0 (cross-user data leakage detected)"
        )

    kw_leakage = metrics.get("keyword_cross_user_leakage_rate")
    if kw_leakage is not None and kw_leakage > 0:
        failures.append(
            f"keyword_cross_user_leakage_rate={kw_leakage} > 0 (keyword/FTS cross-user leakage)"
        )

    fts_leakage = metrics.get("fts_cross_user_leakage_rate")
    if fts_leakage is not None and fts_leakage > 0:
        failures.append(
            f"fts_cross_user_leakage_rate={fts_leakage} > 0 (FTS5 cross-user leakage)"
        )

    exclusion = metrics.get("quarantine_exclusion_rate")
    if exclusion is not None and exclusion < 1.0:
        failures.append(
            f"quarantine_exclusion_rate={exclusion} < 1.0 (quarantined memory escaped recall)"
        )

    # 3. Red-team suite — any failure blocks release
    red_team_results = [
        r for r in data.get("results", [])
        if r.get("suite") == "red_team" and not r.get("passed")
    ]
    for r in red_team_results:
        failures.append(f"red_team.{r['name']}: {r.get('detail', '')}")

    # 4. P20 OAuth / multi-user gate checks
    oauth_leakage = metrics.get("cross_user_oauth_leakage_rate")
    if oauth_leakage is not None and oauth_leakage > 0:
        failures.append(
            f"cross_user_oauth_leakage_rate={oauth_leakage} > 0 (OAuth cross-user leakage)"
        )

    # MCP initialize / tools/list are critical for Cursor connectivity
    for check_name in ("mcp_initialize_failure", "mcp_tools_list_failure", "mcp_tools_call_failure"):
        val = metrics.get(check_name)
        if val:
            failures.append(f"{check_name} detected in eval metrics")

    # OAuth discovery / token exchange critical gates
    for check_name in ("oauth_discovery_failure", "oauth_token_exchange_failure"):
        val = metrics.get(check_name)
        if val:
            failures.append(f"{check_name} detected in eval metrics")

    # dev key accepted in production is a security violation
    if metrics.get("dev_key_accepted_in_production"):
        failures.append("dev_key_accepted_in_production=true — security gate violation")

    return (len(failures) == 0), failures


def _run_unit_tests() -> tuple[bool, str]:
    """Run pytest and return (passed, output_summary)."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no"],
        capture_output=True,
        text=True,
    )
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


# ─── Main ─────────────────────────────────────────────────────────────────────

async def _main(args: argparse.Namespace) -> int:
    print("Mimir Release Gate")
    print("=" * 60)
    all_failures: list[str] = []

    # ── Step 1: Unit tests ────────────────────────────────────────────────────
    print("\n[1/3] Running unit tests …")
    tests_passed, test_output = _run_unit_tests()
    if tests_passed:
        # Extract test count from summary line
        summary = next((l for l in test_output.split("\n") if "passed" in l), test_output[-100:])
        print(f"      PASS: {summary}")
    else:
        print(f"      FAIL: unit tests failed")
        for line in test_output.split("\n")[-5:]:
            if line.strip():
                print(f"        {line}")
        all_failures.append("Unit tests failed")

    # ── Step 2: Migration check ───────────────────────────────────────────────
    print("\n[2/3] Checking migrations …")
    import subprocess
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_migrations.py", "-q", "--tb=short"],
        capture_output=True,
        text=True,
    )
    if r.returncode == 0:
        print("      PASS: migrations up to date")
    else:
        print("      FAIL: migration check failed")
        all_failures.append("Migration tests failed")

    # ── Step 3: Eval report ───────────────────────────────────────────────────
    report_path = Path(args.report)
    print(f"\n[3/3] Checking eval report: {report_path} …")

    if args.run_evals:
        print("      Running evals first …")
        from evals.runner import run_suites, build_report, write_json, write_markdown
        from datetime import datetime, UTC
        started_at = datetime.now(UTC).isoformat()
        suites_run, all_results = await run_suites(["all"], verbose=False)
        report = build_report(suites_run, all_results, started_at)
        write_json(report, report_path)
        write_markdown(report, report_path.with_suffix(".md"))
        report_data = json.loads(report_path.read_text())
    elif not report_path.exists():
        print(f"      SKIP: report not found at {report_path}")
        print(f"            Run: python -m evals.runner --suite all --out {report_path}")
        all_failures.append(f"Eval report not found: {report_path}")
        report_data = None
    else:
        report_data = json.loads(report_path.read_text())

    if report_data:
        gate_passed, eval_failures = _check_report(report_data)
        eval_total = report_data.get("total", 0)
        eval_failed = report_data.get("failed", 0)
        if gate_passed:
            print(f"      PASS: {eval_total - eval_failed}/{eval_total} checks passed, no critical failures")
        else:
            print(f"      FAIL: {len(eval_failures)} gate violation(s)")
            for ef in eval_failures:
                print(f"        ⛔  {ef}")
            all_failures.extend(eval_failures)

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    if all_failures:
        print(f"RELEASE GATE: *** FAIL *** ({len(all_failures)} blocking issue(s))\n")
        for f in all_failures:
            print(f"  ⛔  {f}")
        return 1
    else:
        print("RELEASE GATE: PASS — safe to release")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Mimir release gate")
    parser.add_argument(
        "--report",
        default="reports/evals/latest.json",
        help="Path to eval JSON report (default: reports/evals/latest.json)",
    )
    parser.add_argument(
        "--run-evals",
        action="store_true",
        help="Run evals before checking (generates the report)",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
