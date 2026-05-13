"""Multi-source retrieval orchestration (P6 + P10).

P10 additions:
  - Task category auto-detection (task_categorizer)
  - Adaptive per-provider candidate budget (adaptive_weights)
  - Trust-weighted agreement scoring (confidence)
  - Retrieval confidence estimation (confidence)
  - Returns task_category, provider_contributions, confidence in result
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from retrieval.providers import (
    ProviderHit,
    episodic_recent_provider,
    high_trust_provider,
    identity_provider,
    keyword_provider,
    procedural_provider,
    simulation_provider,
    vector_provider,
)
from retrieval.task_categorizer import categorize
from retrieval.adaptive_weights import compute_provider_weights, compute_provider_limits
from retrieval.confidence import compute_weighted_agreement, estimate_confidence
from context.token_budgeter import count_tokens, trim_to_budget
from memory.trust import MemoryState
from mimir.config import get_settings
from storage.models import Memory

logger = logging.getLogger(__name__)

# States never allowed into context
_BLOCKED = MemoryState.BLOCKED

# States that count against the low-priority cap
_LOW_PRIORITY = {MemoryState.STALE, MemoryState.CONTRADICTED, MemoryState.AGING}

# Composite score weights (unchanged from P6)
_W_TRUST = 0.30
_W_AGREE = 0.25
_W_RECENCY = 0.15
_W_IMPORTANCE = 0.20
_W_BASE = 0.10

# Tier boundary thresholds
_IDENTITY_MIN_IMPORTANCE = 0.8
_IDENTITY_MIN_TRUST = 0.7
_HIGH_TRUST_THRESHOLD = 0.7

PROVIDER_TIMEOUT = 2.0
MAX_CANDIDATES_PER_PROVIDER = 20
MAX_RERANK_SIZE = 100


# ─── Result types ─────────────────────────────────────────────────────────────

@dataclass
class OrchestratorDebug:
    providers: list[str]
    selected: list[dict[str, Any]]
    excluded: list[dict[str, Any]]
    agreement_scores: dict[str, float]      # memory_id -> weighted agreement fraction
    token_cost: int
    ordering_reasons: dict[str, str]        # memory_id -> tier label
    task_category: str = "general"
    provider_weights: dict[str, float] = field(default_factory=dict)
    retrieval_confidence: float = 0.0


@dataclass
class OrchestratorResult:
    memories: list[Memory]
    selected_items: list[dict[str, Any]]    # content/id/layer/… for context builder
    debug: OrchestratorDebug
    task_category: str = "general"
    provider_contributions: dict[str, int] = field(default_factory=dict)
    retrieval_confidence: float = 0.0


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _run_provider(coro, name: str) -> list[ProviderHit]:
    try:
        return await asyncio.wait_for(coro, timeout=PROVIDER_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("Retrieval provider '%s' timed out after %.1fs", name, PROVIDER_TIMEOUT)
        return []
    except Exception as exc:
        logger.warning("Retrieval provider '%s' failed: %s", name, exc)
        return []


def _assign_tier(mem: Memory, project: str | None) -> int:
    """Lower tier number == higher priority in the final context ordering."""
    importance = mem.importance or 0.0
    trust = mem.trust_score or 0.0

    if mem.layer == "semantic" and importance >= _IDENTITY_MIN_IMPORTANCE and trust >= _IDENTITY_MIN_TRUST:
        return 1  # identity / security memories
    if mem.layer == "semantic" and project and mem.project == project:
        return 2  # active project semantic
    if mem.layer == "semantic" and trust >= _HIGH_TRUST_THRESHOLD:
        return 3  # high-trust semantic
    if mem.layer == "episodic":
        return 4  # recent episodic
    if mem.layer == "procedural":
        return 5  # procedural rules
    return 6      # lower-confidence supporting context


_TIER_LABELS = {
    1: "identity_security",
    2: "active_project_semantic",
    3: "high_trust_semantic",
    4: "episodic_recent",
    5: "procedural",
    6: "supporting_context",
}


# ─── Main entrypoint ──────────────────────────────────────────────────────────

async def orchestrate(
    session: AsyncSession,
    query: str,
    *,
    project: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    token_budget: int | None = None,
    max_memories: int | None = None,
    max_episodic: int | None = None,
    max_low_trust: int | None = None,
    task_category: str | None = None,
) -> OrchestratorResult:
    """Run all providers, merge, rerank, filter, and trim to budget.

    P10: auto-detects task category, applies adaptive provider budgets,
    computes trust-weighted agreement, and estimates retrieval confidence.
    """
    settings = get_settings()
    budget = min(token_budget or settings.default_token_budget, settings.max_token_budget)
    max_mems = max_memories or settings.max_memories_per_context
    max_ep = max_episodic if max_episodic is not None else 5
    max_lt = max_low_trust if max_low_trust is not None else 3

    # ── 0. Task categorisation + adaptive weights ──────────────────────────────
    category = task_category or categorize(query)

    # Load provider stats for this category (used for historical weight adjustment)
    # Use a try/except so stats being unavailable never breaks retrieval
    provider_stats_map = {}
    try:
        from retrieval.provider_stats import get_provider_stats
        provider_stats_map = await get_provider_stats(session, project=project, task_category=category)
    except Exception as exc:
        logger.debug("Could not load provider stats: %s", exc)

    weights = compute_provider_weights(category, provider_stats_map)
    limits = compute_provider_limits(weights)

    # Historical usefulness for confidence estimation
    avg_hist_usefulness: float | None = None
    useful_rates = [
        ps.usefulness_rate
        for ps in provider_stats_map.values()
        if ps is not None and (ps.total_sessions or 0) >= 5
    ]
    if useful_rates:
        avg_hist_usefulness = sum(useful_rates) / len(useful_rates)

    # ── 1. Run all providers sequentially (AsyncSession limitation) ────────────
    provider_coros: dict = {
        "vector": vector_provider(
            session, query, project=project, user_id=user_id,
            limit=limits["vector"],
        ),
        "keyword": keyword_provider(
            session, query, project=project, user_id=user_id,
            limit=limits["keyword"],
        ),
        "identity": identity_provider(session, project=project, user_id=user_id),
        "episodic_recent": episodic_recent_provider(
            session, project=project, user_id=user_id, session_id=session_id,
        ),
        "procedural": procedural_provider(session, project=project),
        "high_trust": high_trust_provider(
            session, query, project=project, user_id=user_id,
        ),
    }

    # Simulation evidence provider: active when the query looks like a planning task
    _PLANNING_CATEGORIES = {"procedural", "troubleshooting", "project_continuity", "general"}
    if category in _PLANNING_CATEGORIES:
        provider_coros["simulation"] = simulation_provider(
            session, query, project=project, limit=5,
        )

    gathered = []
    for name, coro in provider_coros.items():
        gathered.append(await _run_provider(coro, name))
    provider_hits: dict[str, list[ProviderHit]] = dict(zip(provider_coros.keys(), gathered))
    active_providers = [name for name, hits in provider_hits.items() if hits]

    # ── 2. Merge hits by memory_id ────────────────────────────────────────────
    merged: dict[str, list[ProviderHit]] = {}
    for hits in provider_hits.values():
        for hit in hits:
            merged.setdefault(hit.memory_id, []).append(hit)

    candidate_ids = list(merged.keys())[:MAX_RERANK_SIZE]
    if not candidate_ids:
        return _empty_result(active_providers, category, weights)

    # ── 3. Load Memory ORM objects ────────────────────────────────────────────
    result = await session.execute(
        select(Memory).where(
            Memory.id.in_(candidate_ids),
            Memory.deleted_at.is_(None),
        )
    )
    mem_map: dict[str, Memory] = {m.id: m for m in result.scalars()}

    # ── 4. Filter blocked; compute trust-weighted agreement + composite scores ─

    # Load graph boosts for all candidates (P11 — never blocks retrieval on failure)
    graph_boosts: dict[str, float] = {}
    try:
        from graph.graph_queries import compute_graph_boost
        graph_boosts = await compute_graph_boost(session, candidate_ids)
    except Exception as exc:
        logger.debug("Graph boost unavailable: %s", exc)

    excluded_debug: list[dict[str, Any]] = []
    now = datetime.utcnow()
    total_providers = len(provider_coros)
    agreement_scores: dict[str, float] = {}
    provider_sources_per_mem: dict[str, set[str]] = {}
    candidates: list[tuple[str, float, int]] = []

    for mid in candidate_ids:
        mem = mem_map.get(mid)
        if mem is None:
            excluded_debug.append({"id": mid, "excluded_reason": "not_found_in_db"})
            continue

        state = mem.memory_state or MemoryState.ACTIVE
        if state in _BLOCKED:
            excluded_debug.append({"id": mid, "excluded_reason": f"blocked_state:{state}"})
            continue

        hits_for_mem = merged[mid]
        unique_sources = {h.retrieval_source for h in hits_for_mem}
        provider_sources_per_mem[mid] = unique_sources

        # Trust-weighted agreement (P10 Priority 5)
        w_agreement = compute_weighted_agreement({mid: unique_sources}, total_providers)
        agreement_scores[mid] = round(w_agreement, 4)

        base_score = max(h.score for h in hits_for_mem)
        trust = mem.trust_score or 0.5
        age_days = (now - mem.created_at).total_seconds() / 86400 if mem.created_at else 90
        recency = max(0.0, 1.0 - age_days / 90)
        importance = mem.importance or 0.5

        graph_boost = graph_boosts.get(mid, 0.0)
        final = round(
            _W_TRUST * trust
            + _W_AGREE * w_agreement
            + _W_RECENCY * recency
            + _W_IMPORTANCE * importance
            + _W_BASE * base_score
            + graph_boost,
            4,
        )
        tier = _assign_tier(mem, project)
        candidates.append((mid, final, tier))

    candidates.sort(key=lambda x: (x[2], -x[1], x[0]))

    # ── 5. Per-category caps ──────────────────────────────────────────────────
    episodic_count = 0
    low_trust_count = 0
    ordered_ids: list[str] = []
    cap_excluded: list[dict[str, Any]] = []

    for mid, _score, _tier in candidates:
        mem = mem_map[mid]
        state = mem.memory_state or MemoryState.ACTIVE

        if mem.layer == "episodic":
            if episodic_count >= max_ep:
                cap_excluded.append({"id": mid, "excluded_reason": "max_episodic_exceeded"})
                continue
            episodic_count += 1

        if state in _LOW_PRIORITY:
            if low_trust_count >= max_lt:
                cap_excluded.append({"id": mid, "excluded_reason": "max_low_priority_exceeded"})
                continue
            low_trust_count += 1

        ordered_ids.append(mid)
        if len(ordered_ids) >= max_mems:
            break

    excluded_debug.extend(cap_excluded)

    # ── 6. Token budget trim ──────────────────────────────────────────────────
    pre_budget_items = [
        {
            "id": mid,
            "content": mem_map[mid].content,
            "layer": mem_map[mid].layer,
            "importance": mem_map[mid].importance,
            "trust_score": mem_map[mid].trust_score,
            "verification_status": mem_map[mid].verification_status,
            "memory_state": mem_map[mid].memory_state,
        }
        for mid in ordered_ids
    ]
    selected_items = trim_to_budget(pre_budget_items, budget)
    selected_ids = {item["id"] for item in selected_items}

    for mid in ordered_ids:
        if mid not in selected_ids:
            excluded_debug.append({"id": mid, "excluded_reason": "token_budget_exceeded"})

    # ── 7. Retrieval confidence estimation (P10 Priority 6) ───────────────────
    selected_mems = [mem_map[item["id"]] for item in selected_items]
    avg_trust = (
        sum(m.trust_score or 0.5 for m in selected_mems) / len(selected_mems)
        if selected_mems else 0.5
    )
    selected_states = [m.memory_state or MemoryState.ACTIVE for m in selected_mems]

    # Token efficiency estimate for confidence
    cumulative_tokens_est = sum(count_tokens(item["content"]) for item in selected_items)
    if budget > 0 and cumulative_tokens_est > 0:
        ratio = cumulative_tokens_est / budget
        token_eff = max(0.0, 1.0 - abs(1.0 - min(ratio, 2.0)))
    else:
        token_eff = 0.5

    # Weighted agreement across selected memories
    selected_sources = {mid: provider_sources_per_mem.get(mid, set()) for mid in selected_ids}
    w_agree_global = compute_weighted_agreement(selected_sources, total_providers)

    confidence = estimate_confidence(
        w_agree_global,
        avg_trust,
        selected_states,
        token_eff,
        avg_hist_usefulness,
    )

    # ── 8. Provider contributions (P10 Priority 1) ────────────────────────────
    provider_contributions: dict[str, int] = {}
    for item in selected_items:
        mid = item["id"]
        for src in provider_sources_per_mem.get(mid, set()):
            provider_contributions[src] = provider_contributions.get(src, 0) + 1

    # ── 9. Debug output ────────────────────────────────────────────────────────
    ordering_reasons: dict[str, str] = {}
    debug_selected: list[dict[str, Any]] = []
    score_lookup = {mid: score for mid, score, _ in candidates}
    tier_lookup = {mid: tier for mid, _, tier in candidates}
    cumulative = 0

    for item in selected_items:
        mid = item["id"]
        mem = mem_map[mid]
        tok = count_tokens(item["content"])
        cumulative += tok
        tier = tier_lookup.get(mid, 6)
        tier_label = _TIER_LABELS.get(tier, "supporting_context")
        ordering_reasons[mid] = tier_label
        sources = sorted(provider_sources_per_mem.get(mid, set()))
        debug_selected.append({
            "id": mid,
            "layer": mem.layer,
            "score": score_lookup.get(mid, 0.0),
            "trust_score": mem.trust_score,
            "agreement_score": agreement_scores.get(mid, 0.0),
            "provider_sources": sources,
            "memory_state": mem.memory_state,
            "token_cost": tok,
            "cumulative_tokens": cumulative,
            "ordering_reason": tier_label,
        })

    # ── 10. Update retrieval frequency ────────────────────────────────────────
    await _bump_retrieval_counts(session, selected_mems)

    return OrchestratorResult(
        memories=selected_mems,
        selected_items=selected_items,
        debug=OrchestratorDebug(
            providers=active_providers,
            selected=debug_selected,
            excluded=excluded_debug,
            agreement_scores=agreement_scores,
            token_cost=cumulative,
            ordering_reasons=ordering_reasons,
            task_category=category,
            provider_weights=weights,
            retrieval_confidence=confidence,
        ),
        task_category=category,
        provider_contributions=provider_contributions,
        retrieval_confidence=confidence,
    )


async def _bump_retrieval_counts(session: AsyncSession, memories: list[Memory]) -> None:
    if not memories:
        return
    now = datetime.utcnow()
    for mem in memories:
        mem.times_retrieved = (mem.times_retrieved or 0) + 1
        mem.last_retrieved_at = now
        session.add(mem)
    try:
        await session.commit()
    except Exception:
        await session.rollback()


def _empty_result(
    active_providers: list[str],
    task_category: str = "general",
    provider_weights: dict[str, float] | None = None,
) -> OrchestratorResult:
    return OrchestratorResult(
        memories=[],
        selected_items=[],
        debug=OrchestratorDebug(
            providers=active_providers,
            selected=[],
            excluded=[],
            agreement_scores={},
            token_cost=0,
            ordering_reasons={},
            task_category=task_category,
            provider_weights=provider_weights or {},
            retrieval_confidence=0.0,
        ),
        task_category=task_category,
        provider_contributions={},
        retrieval_confidence=0.0,
    )
