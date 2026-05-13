"""Pydantic request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ─── Events ──────────────────────────────────────────────────────────────────

class EventIn(BaseModel):
    type: str
    content: str | None = None
    summary: str | None = None
    correction: str | None = None
    lesson: str | None = None
    result: str | None = None
    project: str | None = None
    session_id: str | None = None
    user_id: str | None = None
    tools_used: list[str] | None = None
    meta: dict[str, Any] | None = None


class RecallRequest(BaseModel):
    query: str
    project: str | None = None
    session_id: str | None = None
    user_id: str | None = None
    layer: str | None = None
    limit: int = 10
    min_score: float = 0.3
    token_budget: int | None = None


class RecallFeedbackIn(BaseModel):
    memory_id: str
    outcome: str   # success | failure | irrelevant | harmful
    reason: str | None = None


class RetrievalSessionOutcomeIn(BaseModel):
    """Report the outcome of a task that used a retrieval session."""
    task_outcome: str           # success | failure | partial
    rollback_id: str | None = None
    has_correction: bool = False
    has_harmful_outcome: bool = False


class RecallDebug(BaseModel):
    providers: list[str] = Field(default_factory=list)
    selected: list[dict[str, Any]] = Field(default_factory=list)
    excluded: list[dict[str, Any]] = Field(default_factory=list)
    agreement_scores: dict[str, float] = Field(default_factory=dict)
    token_cost: int = 0


# ─── Memory ──────────────────────────────────────────────────────────────────

class MemoryIn(BaseModel):
    content: str
    layer: str = "episodic"
    project: str | None = None
    session_id: str | None = None
    user_id: str | None = None
    importance: float = 0.5
    meta: dict[str, Any] | None = None


class MemoryPatch(BaseModel):
    content: str | None = None
    importance: float | None = None
    meta: dict[str, Any] | None = None


class MemoryOut(BaseModel):
    id: str
    layer: str
    content: str
    project: str | None
    importance: float
    access_count: int
    created_at: datetime
    updated_at: datetime
    # Temporal
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    memory_state: str | None = None
    superseded_by: str | None = None
    # Trust / provenance
    trust_score: float | None = None
    verification_status: str | None = None
    confidence: float | None = None
    source_type: str | None = None
    poisoning_flags: list | None = None
    quarantine_reason: str | None = None

    model_config = {"from_attributes": True}


# ─── Skills ──────────────────────────────────────────────────────────────────

class SkillProposeIn(BaseModel):
    name: str
    purpose: str
    trigger_conditions: list[dict] = []
    steps: list[dict] = []
    tools_required: list[str] = []
    permissions_required: list[str] = []
    test_cases: list[dict] = []
    project: str | None = None
    meta: dict | None = None


class SkillRunIn(BaseModel):
    input_data: dict[str, Any] | None = None


class SkillResultIn(BaseModel):
    run_id: str
    outcome: str  # success|failure|error
    output_data: dict[str, Any] | None = None


class SkillOut(BaseModel):
    id: str
    name: str
    purpose: str
    status: str
    version: int
    success_count: int
    failure_count: int
    project: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Reflections ─────────────────────────────────────────────────────────────

class ReflectionLogIn(BaseModel):
    trigger: str = "manual"
    observations: list[str]
    lessons: list[str]
    proposed_improvements: list[dict] = []
    project: str | None = None
    session_id: str | None = None


class ReflectionOut(BaseModel):
    id: str
    trigger: str
    observations: list
    lessons: list
    proposed_improvements: list
    project: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Improvements ────────────────────────────────────────────────────────────

class ImprovementProposeIn(BaseModel):
    improvement_type: str
    title: str
    reason: str
    current_behavior: str
    proposed_behavior: str
    expected_benefit: str
    risk: str = "low"
    project: str | None = None
    meta: dict | None = None


class ImprovementOut(BaseModel):
    id: str
    improvement_type: str
    title: str
    reason: str
    current_behavior: str
    proposed_behavior: str
    risk: str
    status: str
    test_result: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Approvals ───────────────────────────────────────────────────────────────

class ApprovalDecisionIn(BaseModel):
    reviewer_note: str | None = None


class ApprovalOut(BaseModel):
    id: str
    title: str
    request_type: str
    status: str
    summary: dict
    notification_sent: bool
    reviewer_note: str | None = None
    decided_at: datetime | None = None
    created_at: datetime
    expires_at: datetime | None

    model_config = {"from_attributes": True}


# ─── General ─────────────────────────────────────────────────────────────────

class StatusOut(BaseModel):
    ok: bool
    message: str | None = None


class PushSubscriptionIn(BaseModel):
    endpoint: str
    keys: dict[str, str]
    user_agent: str | None = None
