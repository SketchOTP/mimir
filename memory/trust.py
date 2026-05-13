"""Memory state and trust level constants.

MemoryState controls lifecycle and recall eligibility.
TrustLevel (verification_status) describes the origin and reliability of a memory.
"""

from __future__ import annotations


class MemoryState:
    ACTIVE = "active"
    AGING = "aging"
    STALE = "stale"
    CONTRADICTED = "contradicted"
    QUARANTINED = "quarantined"
    ARCHIVED = "archived"
    DELETED = "deleted"

    # Never enter any retrieval or context path
    BLOCKED: frozenset[str] = frozenset({QUARANTINED, ARCHIVED, DELETED})
    # Additionally excluded from high-priority / identity context
    HIGH_PRIORITY_EXCLUDED: frozenset[str] = frozenset({QUARANTINED, ARCHIVED, DELETED, STALE, CONTRADICTED})


class TrustLevel:
    TRUSTED_USER_EXPLICIT = "trusted_user_explicit"      # User stated directly
    TRUSTED_SYSTEM_OBSERVED = "trusted_system_observed"  # System recorded reliably
    INFERRED_LOW_CONFIDENCE = "inferred_low_confidence"  # Derived, not confirmed
    EXTERNAL_UNVERIFIED = "external_unverified"          # Third-party, not vetted
    CONFLICTING = "conflicting"                          # Contradicts another memory
    QUARANTINED = "quarantined"                          # Suspected poisoning/injection


# Default trust profiles keyed by (source_type, event_type_hint)
# Each entry: (verification_status, trust_score, confidence)
_TRUST_DEFAULTS: dict[str, tuple[str, float, float]] = {
    "user_correction":      (TrustLevel.TRUSTED_USER_EXPLICIT, 0.95, 0.98),
    "user_explicit":        (TrustLevel.TRUSTED_USER_EXPLICIT, 0.90, 0.85),
    "system_observed":      (TrustLevel.TRUSTED_SYSTEM_OBSERVED, 0.70, 0.70),
    "inferred":             (TrustLevel.INFERRED_LOW_CONFIDENCE, 0.50, 0.55),
    "external_unverified":  (TrustLevel.EXTERNAL_UNVERIFIED, 0.40, 0.45),
}

_DEFAULT_PROFILE = _TRUST_DEFAULTS["system_observed"]


def trust_defaults(source_type: str | None = None) -> tuple[str, float, float]:
    """Return (verification_status, trust_score, confidence) for a source type."""
    if source_type and source_type in _TRUST_DEFAULTS:
        return _TRUST_DEFAULTS[source_type]
    return _DEFAULT_PROFILE
