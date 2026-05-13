"""Static scan: verify no executable Python code contains forbidden Tailscale commands."""

import re
from pathlib import Path

# Commands that must never appear in executable Python source
FORBIDDEN_PATTERNS = [
    r'tailscale\s+up\b',
    r'tailscale\s+down\b',
    r'tailscale\s+logout\b',
    r'tailscale\s+set\b',
    r'systemctl\s+restart\s+tailscaled\b',
]

_COMPILED = [re.compile(p) for p in FORBIDDEN_PATTERNS]

# Directories that contain executable Python code (excludes tests themselves)
_SOURCE_ROOTS = [
    "api", "memory", "skills", "reflections", "approvals",
    "notifications", "context", "metrics", "retrieval",
    "sdk", "storage", "worker", "mcp", "mimir",
]


def _collect_python_files(project_root: Path) -> list[Path]:
    files = []
    for root in _SOURCE_ROOTS:
        for f in (project_root / root).rglob("*.py"):
            files.append(f)
    return files


def test_no_tailscale_commands_in_source():
    project_root = Path(__file__).parent.parent
    py_files = _collect_python_files(project_root)
    assert py_files, "No Python source files found to scan"

    violations: list[str] = []
    for path in py_files:
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            # Skip comment-only lines
            if stripped.startswith("#"):
                continue
            for pattern in _COMPILED:
                if pattern.search(line):
                    violations.append(f"{path.relative_to(project_root)}:{line_no}: {line.strip()}")

    assert not violations, (
        "Forbidden Tailscale command(s) found in executable source:\n"
        + "\n".join(violations)
    )


def test_forbidden_patterns_are_comprehensive():
    """Sanity check that our pattern list covers all required forbidden commands."""
    required_commands = [
        "tailscale up",
        "tailscale down",
        "tailscale logout",
        "tailscale set",
        "systemctl restart tailscaled",
    ]
    for cmd in required_commands:
        matched = any(p.search(cmd) for p in _COMPILED)
        assert matched, f"Pattern list does not cover: {cmd!r}"
