"""Re-rank retrieved memories by relevance, recency, and importance."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def rank(
    hits: list[dict[str, Any]],
    *,
    recency_weight: float = 0.2,
    importance_weight: float = 0.3,
    score_weight: float = 0.5,
) -> list[dict[str, Any]]:
    """
    hits: list of {memory, score, layer}
    Returns re-ranked list with combined_score added.
    """
    now = datetime.utcnow()

    for hit in hits:
        mem = hit["memory"]
        base_score = hit["score"]

        # Recency: decay over 90 days
        age_days = (now - mem.created_at).total_seconds() / 86400 if mem.created_at else 90
        recency = max(0.0, 1.0 - age_days / 90)

        importance = getattr(mem, "importance", 0.5)

        combined = (
            score_weight * base_score
            + recency_weight * recency
            + importance_weight * importance
        )
        hit["combined_score"] = round(combined, 4)
        hit["recency"] = round(recency, 3)

    hits.sort(key=lambda x: x["combined_score"], reverse=True)
    return hits


def filter_by_layer_priority(hits: list[dict], layers_order: list[str]) -> list[dict]:
    """Sort hits so higher-priority layers appear first within the same score band."""
    priority = {l: i for i, l in enumerate(layers_order)}
    hits.sort(key=lambda x: (-(x.get("combined_score", 0)), priority.get(x["layer"], 99)))
    return hits
