"""Count tokens and enforce budgets to prevent context blowup."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_encoder = None


def _get_encoder():
    global _encoder
    if _encoder is None:
        try:
            import tiktoken
            _encoder = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _encoder = None
    return _encoder


def count_tokens(text: str) -> int:
    enc = _get_encoder()
    if enc:
        return len(enc.encode(text))
    # Rough fallback: ~4 chars per token
    return len(text) // 4


def fits_in_budget(texts: list[str], budget: int) -> bool:
    return sum(count_tokens(t) for t in texts) <= budget


def trim_to_budget(items: list[dict], budget: int, content_key: str = "content") -> list[dict]:
    """Return as many items as fit within the token budget (highest priority first)."""
    selected = []
    used = 0
    for item in items:
        content = item.get(content_key, "")
        cost = count_tokens(content)
        if used + cost <= budget:
            selected.append(item)
            used += cost
        else:
            break
    return selected
