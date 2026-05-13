"""Generate a release report summarising gate results, test count, and build artifacts.

Called by `make release` after all checks pass.

Usage:
    python -m evals.release_report --out reports/release/latest
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, UTC
from pathlib import Path


def _run(cmd: list[str]) -> tuple[int, str]:
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout + result.stderr


def _load_json(path: str) -> dict | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return None


def generate_release_report(out_stem: Path) -> dict:
    out_stem.parent.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(UTC).isoformat()

    report: dict = {
        "created_at": created_at,
        "version": "0.1.0",
        "steps": {},
        "passed": False,
    }

    # 1. Unit tests
    print("[release] Running unit tests...")
    rc, output = _run([sys.executable, "-m", "pytest", "tests/", "-q", "--tb=short"])
    test_lines = [l for l in output.splitlines() if "passed" in l or "failed" in l or "error" in l]
    report["steps"]["tests"] = {
        "passed": rc == 0,
        "returncode": rc,
        "summary": test_lines[-1] if test_lines else output[-200:],
    }
    if rc != 0:
        print(f"[release] FAIL: unit tests\n{output[-500:]}")

    # 2. Migrations
    print("[release] Running migrations...")
    rc, output = _run(["alembic", "upgrade", "head"])
    report["steps"]["migrations"] = {
        "passed": rc == 0,
        "returncode": rc,
        "output": output.strip()[-300:],
    }
    if rc != 0:
        print(f"[release] FAIL: migrations\n{output}")

    # 3. Evals
    print("[release] Running eval harness...")
    rc, output = _run([sys.executable, "-m", "evals.runner", "--suite", "all",
                       "--out", "reports/evals/latest.json"])
    eval_report = _load_json("reports/evals/latest.json")
    report["steps"]["evals"] = {
        "passed": rc == 0 and (eval_report or {}).get("passed", False),
        "returncode": rc,
        "suite_count": len((eval_report or {}).get("suites", [])),
        "critical_failures": (eval_report or {}).get("critical_failures", []),
    }
    if rc != 0:
        print(f"[release] FAIL: evals\n{output[-300:]}")

    # 4. Release gate
    print("[release] Running release gate...")
    rc, output = _run([sys.executable, "-m", "evals.release_gate"])
    report["steps"]["gate"] = {
        "passed": rc == 0,
        "returncode": rc,
        "output": output.strip()[-300:],
    }
    if rc != 0:
        print(f"[release] FAIL: release gate\n{output[-300:]}")

    # 5. Build web
    print("[release] Building web UI...")
    if Path("web").exists():
        rc, output = _run(["npm", "run", "build"], )
        # re-run with cwd
        result = subprocess.run(["npm", "run", "build"], capture_output=True, text=True, cwd="web")
        rc = result.returncode
        output = result.stdout + result.stderr
        dist_exists = Path("web/dist").exists()
        report["steps"]["web_build"] = {
            "passed": rc == 0 and dist_exists,
            "returncode": rc,
            "dist_exists": dist_exists,
        }
    else:
        report["steps"]["web_build"] = {"passed": True, "skipped": True}

    # 6. Build wheel
    print("[release] Building wheel...")
    rc, output = _run([sys.executable, "-m", "build", "--wheel", "--outdir", "dist/"])
    if rc != 0:
        # build not installed — skip gracefully
        rc, output = _run([sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "-w", "dist/"])
    wheel_files = list(Path("dist").glob("*.whl")) if Path("dist").exists() else []
    report["steps"]["wheel"] = {
        "passed": len(wheel_files) > 0,
        "wheels": [str(w) for w in wheel_files],
    }

    # Final pass/fail
    all_passed = all(s.get("passed", False) for s in report["steps"].values())
    report["passed"] = all_passed

    # Load gate report
    gate_report = _load_json("reports/gate/latest.json")
    if gate_report:
        report["gate_report"] = gate_report

    # Write JSON
    json_path = out_stem.with_suffix(".json")
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2)

    # Write Markdown
    md_path = out_stem.with_suffix(".md")
    _write_markdown(md_path, report)

    print(f"\n[release] {'PASSED' if all_passed else 'FAILED'} — report at {json_path}")
    for step, result in report["steps"].items():
        icon = "✓" if result.get("passed") else ("~" if result.get("skipped") else "✗")
        print(f"  {icon} {step}")

    return report


def _write_markdown(path: Path, report: dict) -> None:
    lines = [
        f"# Mimir Release Report",
        f"",
        f"**Created:** {report['created_at']}  ",
        f"**Version:** {report['version']}  ",
        f"**Result:** {'✅ PASSED' if report['passed'] else '❌ FAILED'}",
        f"",
        f"## Steps",
        f"",
        f"| Step | Result |",
        f"|------|--------|",
    ]
    for step, result in report["steps"].items():
        if result.get("skipped"):
            icon = "⏭ skipped"
        elif result.get("passed"):
            icon = "✅ pass"
        else:
            icon = "❌ FAIL"
        lines.append(f"| `{step}` | {icon} |")

    lines += ["", "## Details", ""]
    for step, result in report["steps"].items():
        lines.append(f"### {step}")
        summary = result.get("summary") or result.get("output") or ""
        if summary:
            lines += [f"```", summary[:500], "```", ""]
        if "critical_failures" in result and result["critical_failures"]:
            lines.append(f"**Critical failures:** {', '.join(result['critical_failures'])}")
            lines.append("")

    with open(path, "w") as f:
        f.write("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Mimir release report")
    parser.add_argument("--out", type=Path, default=Path("reports/release/latest"),
                        help="Output path stem (no extension)")
    args = parser.parse_args()
    report = generate_release_report(args.out)
    if not report["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
