from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def test_web_vitest_resilience_suite_passes():
    result = subprocess.run(
        ["npm", "run", "test", "--", "--run"],
        cwd=WEB,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def test_web_build_passes():
    result = subprocess.run(
        ["npm", "run", "build"],
        cwd=WEB,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
