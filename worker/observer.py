"""Observer worker — fast, minimal-mutation event capture.

Responsibility:
  - Capture raw events and task traces with minimal processing
  - Write episodic activity records quickly
  - No heavy reasoning, no trust inference, no consolidation

All writes are append-only. The reflector/consolidator process these records
asynchronously on their own schedule.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, UTC
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import Memory, MemoryEvent, Session, TaskTrace

logger = logging.getLogger(__name__)


async def observe_event(
    session: AsyncSession,
    event_type: str,
    data: dict[str, Any],
    memory_id: str | None = None,
    project: str | None = None,
    user_id: str | None = None,
) -> str:
    """Record a raw lifecycle event — fast write, no business logic."""
    event = MemoryEvent(
        id=uuid.uuid4().hex,
        memory_id=memory_id or "_system",
        event_type=event_type,
        detail={
            "data": data,
            "project": project,
            "user_id": user_id,
            "observed_at": datetime.now(UTC).isoformat(),
        },
    )
    session.add(event)
    await session.flush()
    logger.debug("observer: recorded event %s for memory %s", event_type, memory_id)
    return event.id


async def record_task_trace(
    session: AsyncSession,
    session_id: str,
    task_type: str,
    input_summary: str | None = None,
    output_summary: str | None = None,
    tools_used: list[str] | None = None,
    outcome: str | None = None,
    duration_ms: int | None = None,
    meta: dict | None = None,
) -> str:
    """Record a task trace — fast append, no inference."""
    trace = TaskTrace(
        id=uuid.uuid4().hex,
        session_id=session_id,
        task_type=task_type,
        input_summary=input_summary,
        output_summary=output_summary,
        tools_used=tools_used or [],
        outcome=outcome,
        duration_ms=duration_ms,
        meta=meta,
    )
    session.add(trace)
    await session.flush()
    logger.debug("observer: recorded task trace %s (session=%s)", trace.id, session_id)
    return trace.id


async def record_raw_episodic(
    session: AsyncSession,
    content: str,
    project: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    meta: dict | None = None,
) -> str:
    """Store raw episodic activity with minimal metadata — defer classification to reflector."""
    mem = Memory(
        id=uuid.uuid4().hex,
        layer="episodic",
        content=content,
        project=project,
        user_id=user_id,
        session_id=session_id,
        importance=0.3,
        memory_state="active",
        trust_score=0.5,
        verification_status="trusted_system_observed",
        confidence=0.5,
        source_type="system_observed",
        valid_from=datetime.now(UTC),
        meta={"raw": True, **(meta or {})},
    )
    session.add(mem)
    await session.flush()
    logger.debug("observer: stored raw episodic %s (project=%s)", mem.id, project)
    return mem.id
