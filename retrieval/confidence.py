"""Retrieval confidence estimation for P10.

Computes a [0, 1] score summarising how confident we are that the retrieval
session returned high-quality, useful memories.  Used for agent planning and
escalation decisions.
"""

from __future__ import annotations

from memory.trust import MemoryState

# Score component weights (must sum to 1.0)
_W_AGREEMENT = 0.35
_W_TRUST = 0.30
_W_STATE = 0.15
_W_EFFICIENCY = 0.10
_W_HISTORY = 0.10

# Provider agreement trust weights for weighted agreement score
_PROVIDER_TRUST_WEIGHTS: dict[str, float] = {
    "identity": 1.5,
    "high_trust": 1.4,
    "procedural": 1.3,
    "vector": 1.0,
    "episodic_recent": 0.9,
    "keyword": 0.8,
}

_MAX_POSSIBLE_PROVIDER_WEIGHT = sum(
    sorted(_PROVIDER_TRUST_WEIGHTS.values(), reverse=True)[:2]  # top-2 providers agreeing
)


def compute_weighted_agreement(
    provider_sources_per_memory: dict[str, set[str]],
    total_providers: int,
) -> float:
    """Compute agreement as trust-weighted fraction.

    Higher weight when high-trust providers (identity, high_trust, procedural)
    agree vs when only weak providers (keyword) agree.
    """
    if not provider_sources_per_memory or total_providers == 0:
        return 0.0

    scores = []
    for sources in provider_sources_per_memory.values():
        if not sources:
            scores.append(0.0)
            continue
        # Weighted sum of agreeing providers
        w_sum = sum(_PROVIDER_TRUST_WEIGHTS.get(p, 1.0) for p in sources)
        # Max possible: all providers agree with their full weights
        max_w = sum(_PROVIDER_TRUST_WEIGHTS.get(p, 1.0) for p in _PROVIDER_TRUST_WEIGHTS)
        scores.append(min(1.0, w_sum / max_w) if max_w > 0 else 0.0)

    return round(sum(scores) / len(scores), 4) if scores else 0.0


def estimate_confidence(
    weighted_agreement: float,
    avg_trust: float,
    memory_states: list[str],
    token_efficiency: float,
    historical_usefulness: float | None = None,
) -> float:
    """Compute retrieval confidence score in [0, 1].

    Components:
      - agreement: cross-provider consensus (trust-weighted)
      - trust: mean trust of retrieved memories
      - state: fraction of memories in ACTIVE state
      - efficiency: token budget utilisation
      - history: historical provider usefulness (from ProviderStats)
    """
    blocked = set(MemoryState.BLOCKED)

    if memory_states:
        active_count = sum(1 for s in memory_states if s == MemoryState.ACTIVE)
        active_fraction = active_count / len(memory_states)
    else:
        active_fraction = 0.5  # unknown — use neutral

    hist = historical_usefulness if historical_usefulness is not None else 0.5

    confidence = (
        _W_AGREEMENT * weighted_agreement
        + _W_TRUST * avg_trust
        + _W_STATE * active_fraction
        + _W_EFFICIENCY * token_efficiency
        + _W_HISTORY * hist
    )
    return round(min(1.0, max(0.0, confidence)), 4)
