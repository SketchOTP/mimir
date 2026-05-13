"""Lightweight task category detection for P10 adaptive retrieval.

Classifies a query into one of six task categories so the orchestrator can
apply category-appropriate provider weights and budget allocation.
"""

from __future__ import annotations

import re

# Ordered pattern list — first match wins.
# Each entry: (category, keyword_patterns)
_PATTERNS: list[tuple[str, list[str]]] = [
    ("identity", [
        "who am i", "my name", "about me", "my profile", "my background",
        "i prefer", "my preferences", "user preferences", "my settings",
        "my role", "who is the user", "about the user",
    ]),
    ("project_continuity", [
        "last time", "previous session", "where did i", "what was i", "what were we",
        "continue from", "resume", "ongoing", "pick up", "where we left",
        "last session", "previous work", "what did i", "catch me up",
    ]),
    ("troubleshooting", [
        "error", "exception", "traceback", "stack trace", "fix", "broken",
        "not working", "fails", "failed", "crash", "debug", "why is", "why does",
        "why won't", "problem with", "issue with", "can't", "cannot", "doesn't work",
    ]),
    ("procedural", [
        "how to", "how do i", "steps to", "step by step", "walk me through",
        "procedure", "workflow", "process", "guide", "instructions", "tutorial",
        "what is the way", "best way to", "proper way",
    ]),
    ("configuration", [
        "config", "configuration", "setting", "settings", "option", "parameter",
        "setup", "configure", "enable", "disable", "flag", "env", "environment variable",
        "variable", "yaml", "json config", "toml", "ini file",
    ]),
]

_VALID_CATEGORIES = frozenset(
    {"identity", "procedural", "troubleshooting", "project_continuity", "configuration", "general"}
)


def categorize(query: str) -> str:
    """Return the task category for a query string.

    Uses keyword pattern matching — first matching category wins.  Falls back
    to 'general' if no pattern matches.

    Returns one of: identity | procedural | troubleshooting |
                    project_continuity | configuration | general
    """
    q = query.lower()
    # Strip punctuation for cleaner matching
    q_clean = re.sub(r"[^\w\s]", " ", q)

    for category, patterns in _PATTERNS:
        for pattern in patterns:
            if pattern in q_clean:
                return category

    return "general"
