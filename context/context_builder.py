"""Build token-efficient context from memory layers for a given query.

P6: all retrieval now flows through the orchestrator; direct vector/identity
calls have been removed from this module.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from retrieval.orchestrator import orchestrate
from context.token_budgeter import count_tokens
from storage.models import ContextBuild
from mimir.config import get_settings


async def build(
    session: AsyncSession,
    query: str,
    *,
    project: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    token_budget: int | None = None,
    include_identity: bool = True,
) -> dict[str, Any]:
    """
    Build context for the given query via the P6 retrieval orchestrator.
    Returns {context_string, memory_ids, token_count, budget_used, memories, build_id, debug}.
    """
    settings = get_settings()
    raw_budget = token_budget or settings.default_token_budget
    budget = min(raw_budget, settings.max_token_budget)

    orch = await orchestrate(
        session,
        query,
        project=project,
        session_id=session_id,
        user_id=user_id,
        token_budget=budget,
        max_memories=settings.max_memories_per_context,
    )

    # Build context string
    context_parts = [
        f"[{item['layer']}] {item['content']}"
        for item in orch.selected_items
    ]
    context_string = "\n".join(context_parts)
    # Use the orchestrator's tracked token cost (measured on content before
    # prefix formatting) so the reported cost matches what trim_to_budget enforced.
    token_count = orch.debug.token_cost
    memory_ids = [item["id"] for item in orch.selected_items]
    relevance_scores = [
        entry.get("score") for entry in orch.debug.selected
    ]

    # Persist context build record
    build_rec = ContextBuild(
        id=f"ctx_{uuid.uuid4().hex[:16]}",
        query=query,
        session_id=session_id,
        project=project,
        memory_ids=memory_ids,
        token_count=token_count,
        budget_used=token_count / budget if budget else 0,
        relevance_scores=relevance_scores,
    )
    session.add(build_rec)
    await session.commit()

    # Shape debug to match the P6 schema expected by the API/tests
    debug = {
        "providers": orch.debug.providers,
        "selected": orch.debug.selected,
        "excluded": orch.debug.excluded,
        "agreement_scores": orch.debug.agreement_scores,
        "token_cost": orch.debug.token_cost,
        "ordering_reasons": orch.debug.ordering_reasons,
        # P10 additions
        "task_category": orch.debug.task_category,
        "provider_weights": orch.debug.provider_weights,
        "retrieval_confidence": orch.debug.retrieval_confidence,
        # Legacy keys kept for backward compatibility
        "excluded_top": orch.debug.excluded[:5],
        "budget": budget,
    }

    return {
        "context_string": context_string,
        "memory_ids": memory_ids,
        "token_count": token_count,
        "budget_used": token_count / budget if budget else 0,
        "memories": [
            {"id": item["id"], "layer": item["layer"], "content": item["content"]}
            for item in orch.selected_items
        ],
        "build_id": build_rec.id,
        "debug": debug,
    }
