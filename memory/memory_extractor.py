"""Extract, classify, and assign trust to memory candidates from raw events."""

from __future__ import annotations

import re
from typing import Any

from memory.trust import TrustLevel, trust_defaults

# Classification keywords for each memory layer
_SEMANTIC_SIGNALS = [
    r"\bprefer\b", r"\balways\b", r"\bnever\b", r"\bcall me\b", r"\bmy name\b",
    r"\bmy role\b", r"\brule\b", r"\bpolicy\b", r"\bfact\b", r"\bdefinition\b",
    r"\bimportant\b", r"\bcritical\b",
]

_PROCEDURAL_SIGNALS = [
    r"\bstep\b", r"\bworkflow\b", r"\bprocedure\b", r"\bhow to\b", r"\bprocess\b",
    r"\brunbook\b", r"\bprotocol\b", r"\brecipe\b", r"\binstruction\b",
]

_EPHEMERAL_SIGNALS = [
    r"\btoday\b", r"\bright now\b", r"\bcurrently\b", r"\bthis session\b",
    r"\btemporarily\b", r"\bfor now\b", r"\bthis week\b",
]

# Signals indicating the user is making a direct, explicit personal statement
_IDENTITY_SIGNALS = [
    r"\bcall me\b", r"\bmy name\b", r"\bmy role\b", r"\bi prefer\b",
    r"\bi always\b", r"\bi never\b", r"\bdon't call me\b", r"\bnever call me\b",
]


def classify(content: str) -> str:
    """Classify content into episodic|semantic|procedural layer."""
    text = content.lower()

    proc_score = sum(1 for p in _PROCEDURAL_SIGNALS if re.search(p, text))
    sem_score = sum(1 for p in _SEMANTIC_SIGNALS if re.search(p, text))
    eph_score = sum(1 for p in _EPHEMERAL_SIGNALS if re.search(p, text))

    if eph_score > 0 and eph_score >= sem_score:
        return "episodic"
    if proc_score >= 2 or (proc_score > sem_score):
        return "procedural"
    if sem_score >= 1:
        return "semantic"
    return "episodic"


def _is_identity_statement(content: str) -> bool:
    """Return True if content is a direct personal/preference statement by the user."""
    text = content.lower()
    return any(re.search(p, text) for p in _IDENTITY_SIGNALS)


def is_identity_statement(content: str) -> bool:
    """Public wrapper for identity/preference classification."""
    return _is_identity_statement(content)


def extract_importance(content: str, event_type: str | None = None) -> float:
    """Score importance 0.0–1.0 based on signals."""
    text = content.lower()
    score = 0.5

    high_signals = [r"\bcritical\b", r"\bimportant\b", r"\bprefer\b", r"\bnever\b", r"\balways\b", r"\bcall me\b"]
    low_signals = [r"\bmaybe\b", r"\bperhaps\b", r"\btemporarily\b", r"\bfor now\b"]

    for pat in high_signals:
        if re.search(pat, text):
            score = min(1.0, score + 0.1)
    for pat in low_signals:
        if re.search(pat, text):
            score = max(0.1, score - 0.1)

    if event_type == "user_correction":
        score = min(1.0, score + 0.2)
    if event_type == "outcome" and "failure" in text:
        score = min(1.0, score + 0.15)

    return round(score, 2)


def extract_trust_info(content: str, event_type: str | None = None) -> dict[str, Any]:
    """
    Determine trust fields for a memory candidate.

    Returns dict with: verification_status, trust_score, confidence, source_type.
    """
    if event_type == "user_correction":
        vs, ts, conf = trust_defaults("user_correction")
        return {
            "verification_status": vs,
            "trust_score": ts,
            "confidence": conf,
            "source_type": "user_correction",
        }

    if _is_identity_statement(content):
        vs, ts, conf = trust_defaults("user_explicit")
        return {
            "verification_status": vs,
            "trust_score": ts,
            "confidence": conf,
            "source_type": "user_explicit",
        }

    # Ephemeral/inferred content gets lower trust
    text = content.lower()
    eph_score = sum(1 for p in _EPHEMERAL_SIGNALS if re.search(p, text))
    if eph_score >= 2:
        vs, ts, conf = trust_defaults("inferred")
        return {
            "verification_status": vs,
            "trust_score": ts,
            "confidence": conf,
            "source_type": "inferred",
        }

    vs, ts, conf = trust_defaults("system_observed")
    return {
        "verification_status": vs,
        "trust_score": ts,
        "confidence": conf,
        "source_type": "system_observed",
    }


def extract_from_event(event: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Parse a raw event dict and return list of memory candidates.

    Each candidate: {content, layer, importance, meta, trust_info}
    """
    candidates = []
    event_type = event.get("type", "")

    # Direct memory content
    if content := event.get("content") or event.get("summary"):
        layer = classify(content)
        candidates.append(
            {
                "content": content,
                "layer": layer,
                "importance": extract_importance(content, event_type),
                "meta": {"source_event_type": event_type, **(event.get("meta") or {})},
                "trust_info": extract_trust_info(content, event_type),
            }
        )

    # User corrections always become high-trust semantic
    if event_type == "user_correction" and (correction := event.get("correction")):
        candidates.append(
            {
                "content": correction,
                "layer": "semantic",
                "importance": 0.9,
                "meta": {"source_event_type": "user_correction"},
                "trust_info": extract_trust_info(correction, "user_correction"),
            }
        )

    # Outcomes with lessons
    if event_type == "outcome" and (lesson := event.get("lesson")):
        candidates.append(
            {
                "content": lesson,
                "layer": "episodic",
                "importance": extract_importance(lesson, event_type),
                "meta": {"source_event_type": "outcome", "outcome": event.get("result")},
                "trust_info": extract_trust_info(lesson, event_type),
            }
        )

    return candidates
