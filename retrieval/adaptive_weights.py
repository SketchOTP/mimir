"""Adaptive provider weight computation for P10.

Combines static category-based boosts with slow historically-learned adjustments.
All weights are bounded to prevent instability.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from storage.models import ProviderStats

# All provider names (must match keys used in orchestrator)
ALL_PROVIDERS = ("vector", "keyword", "identity", "episodic_recent", "procedural", "high_trust")

# Category-based static boosts: provider → weight multiplier
# Sum doesn't need to be 1.0 — these are relative boosts applied to the base budget
_CATEGORY_BOOSTS: dict[str, dict[str, float]] = {
    "identity": {
        "identity": 2.0,
        "high_trust": 1.5,
        "vector": 0.9,
        "keyword": 0.7,
        "episodic_recent": 0.8,
        "procedural": 0.6,
    },
    "project_continuity": {
        "episodic_recent": 2.0,
        "procedural": 1.3,
        "vector": 1.1,
        "high_trust": 1.0,
        "keyword": 0.9,
        "identity": 0.7,
    },
    "troubleshooting": {
        "episodic_recent": 1.8,
        "procedural": 1.6,
        "vector": 1.2,
        "keyword": 1.2,
        "high_trust": 1.0,
        "identity": 0.6,
    },
    "procedural": {
        "procedural": 2.0,
        "high_trust": 1.3,
        "vector": 0.9,
        "keyword": 1.0,
        "identity": 0.7,
        "episodic_recent": 0.7,
    },
    "configuration": {
        "keyword": 1.8,
        "procedural": 1.6,
        "high_trust": 1.3,
        "vector": 1.0,
        "identity": 0.6,
        "episodic_recent": 0.7,
    },
    "general": {
        "vector": 1.0,
        "keyword": 1.0,
        "identity": 1.0,
        "episodic_recent": 1.0,
        "procedural": 1.0,
        "high_trust": 1.0,
    },
}

# Historical adjustment learning rate — slow adaptation
_ALPHA = 0.10

# Usefulness target: we want providers to be useful at least 55% of the time
_TARGET_USEFULNESS = 0.55

# Minimum evidence before historical adjustment kicks in
_MIN_EVIDENCE_SESSIONS = 5

# Bounds relative to the category base weight
_WEIGHT_FLOOR_FRACTION = 0.30   # never below 30% of base
_WEIGHT_CEIL_MULTIPLIER = 2.5   # never above 2.5× base

# Minimum absolute limit per provider (always send at least this many candidates)
_MIN_PROVIDER_LIMIT = 3
_MAX_PROVIDER_LIMIT = 40
_BASE_PROVIDER_LIMIT = 20


def compute_provider_weights(
    task_category: str,
    provider_stats: dict[str, "ProviderStats | None"] | None = None,
) -> dict[str, float]:
    """Compute adaptive weight for each provider given task category + history.

    Returns a dict of {provider_name: weight} where weight is a positive
    multiplier.  Weights are bounded to [floor, ceil] relative to the base.
    """
    boosts = _CATEGORY_BOOSTS.get(task_category, _CATEGORY_BOOSTS["general"])

    weights: dict[str, float] = {}
    for provider in ALL_PROVIDERS:
        base_w = boosts.get(provider, 1.0)

        # Historical adjustment from accumulated stats
        hist_adj = 0.0
        if provider_stats:
            stats = provider_stats.get(provider)
            if stats and (stats.total_sessions or 0) >= _MIN_EVIDENCE_SESSIONS:
                # Shift weight proportional to usefulness above/below target
                hist_adj = _ALPHA * ((stats.usefulness_rate or 0.5) - _TARGET_USEFULNESS)

        adjusted = base_w * (1.0 + hist_adj)

        # Apply drift penalty: halve the adjustment if provider is flagged
        if provider_stats:
            stats = provider_stats.get(provider)
            if stats and stats.drift_flagged:
                adjusted = max(base_w * _WEIGHT_FLOOR_FRACTION, adjusted * 0.7)

        # Bound to [floor, ceil]
        floor = base_w * _WEIGHT_FLOOR_FRACTION
        ceil_ = base_w * _WEIGHT_CEIL_MULTIPLIER
        weights[provider] = round(max(floor, min(ceil_, adjusted)), 4)

    return weights


def compute_provider_limits(weights: dict[str, float]) -> dict[str, int]:
    """Convert weights to per-provider candidate limits."""
    return {
        provider: max(
            _MIN_PROVIDER_LIMIT,
            min(_MAX_PROVIDER_LIMIT, round(_BASE_PROVIDER_LIMIT * weights.get(provider, 1.0))),
        )
        for provider in ALL_PROVIDERS
    }


def update_weight_from_stats(
    old_weight: float,
    usefulness_rate: float,
    base_weight: float,
) -> float:
    """Compute a new weight using slow conservative adaptation.

    Bounded to [base * FLOOR, base * CEIL].  Never oscillates rapidly.
    """
    adjustment = _ALPHA * (usefulness_rate - _TARGET_USEFULNESS) * base_weight
    new_weight = old_weight + adjustment
    floor = base_weight * _WEIGHT_FLOOR_FRACTION
    ceil_ = base_weight * _WEIGHT_CEIL_MULTIPLIER
    return round(max(floor, min(ceil_, new_weight)), 4)
