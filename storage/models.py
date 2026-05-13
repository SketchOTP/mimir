"""All SQLAlchemy ORM models for Mimir."""

from datetime import datetime
from typing import Any
import json

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer, String, Text, JSON,
    func, Index,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """DateTime that strips tzinfo on write so both SQLite and Postgres (TIMESTAMP WITHOUT TIME ZONE) accept it."""
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and hasattr(value, "tzinfo") and value.tzinfo is not None:
            return value.replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        return value


class Base(DeclarativeBase):
    pass


# ─── Users & API Keys ─────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(32), default="user", server_default="user")  # owner|admin|user
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    api_keys: Mapped[list["APIKey"]] = relationship(back_populates="user")


class APIKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    key_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="api_keys")


# ─── Memory ──────────────────────────────────────────────────────────────────

class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    layer: Mapped[str] = mapped_column(String(20))          # episodic|semantic|procedural|working
    content: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    embedding_id: Mapped[str | None] = mapped_column(String(64))
    project: Mapped[str | None] = mapped_column(String(128))
    user_id: Mapped[str | None] = mapped_column(String(64))
    session_id: Mapped[str | None] = mapped_column(String(64))
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    last_accessed: Mapped[datetime | None] = mapped_column(UTCDateTime)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    meta: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    # ── Temporal memory ───────────────────────────────────────────────────────
    valid_from: Mapped[datetime | None] = mapped_column(UTCDateTime)
    valid_to: Mapped[datetime | None] = mapped_column(UTCDateTime)
    superseded_by: Mapped[str | None] = mapped_column(String(64))
    memory_state: Mapped[str] = mapped_column(String(20), default="active", server_default="active")
    last_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    # ── Trust / provenance ────────────────────────────────────────────────────
    trust_score: Mapped[float] = mapped_column(Float, default=0.7, server_default="0.7")
    source_type: Mapped[str | None] = mapped_column(String(64))
    source_id: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str | None] = mapped_column(String(64))
    verified_by: Mapped[str | None] = mapped_column(String(64))
    verification_status: Mapped[str] = mapped_column(
        String(32), default="trusted_system_observed", server_default="trusted_system_observed"
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.7, server_default="0.7")
    poisoning_flags: Mapped[list | None] = mapped_column(JSON)

    # ── Retrieval frequency tracking ──────────────────────────────────────────
    times_retrieved: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_retrieved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    successful_retrievals: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    failed_retrievals: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # ── Procedural learning fields ────────────────────────────────────────────
    evidence_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    derived_from_episode_ids: Mapped[list | None] = mapped_column(JSON)
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_failure_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    events: Mapped[list["MemoryEvent"]] = relationship(back_populates="memory")
    links_from: Mapped[list["MemoryLink"]] = relationship(
        foreign_keys="MemoryLink.source_id", back_populates="source"
    )

    __table_args__ = (
        Index("ix_memories_layer", "layer"),
        Index("ix_memories_project", "project"),
        Index("ix_memories_session", "session_id"),
        Index("ix_memories_state", "memory_state"),
    )

    @property
    def quarantine_reason(self) -> str | None:
        if self.memory_state != "quarantined":
            return None
        if isinstance(self.meta, dict):
            return self.meta.get("quarantine_reason")
        return None


class MemoryEvent(Base):
    __tablename__ = "memory_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    memory_id: Mapped[str] = mapped_column(ForeignKey("memories.id"))
    event_type: Mapped[str] = mapped_column(String(32))   # created|updated|accessed|consolidated|deleted
    detail: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    memory: Mapped["Memory"] = relationship(back_populates="events")


class MemoryLink(Base):
    __tablename__ = "memory_links"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("memories.id"))
    target_id: Mapped[str] = mapped_column(ForeignKey("memories.id"))
    link_type: Mapped[str] = mapped_column(String(32))    # supports|contradicts|supersedes|related
    strength: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    source: Mapped["Memory"] = relationship(foreign_keys=[source_id], back_populates="links_from")


# ─── Sessions & Task Traces ───────────────────────────────────────────────────

class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project: Mapped[str | None] = mapped_column(String(128))
    user_id: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|closed
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[dict | None] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    task_traces: Mapped[list["TaskTrace"]] = relationship(back_populates="session")


class TaskTrace(Base):
    __tablename__ = "task_traces"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("sessions.id"))
    task_type: Mapped[str] = mapped_column(String(64))
    input_summary: Mapped[str | None] = mapped_column(Text)
    output_summary: Mapped[str | None] = mapped_column(Text)
    tools_used: Mapped[list | None] = mapped_column(JSON)
    outcome: Mapped[str | None] = mapped_column(String(32))  # success|failure|partial
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    meta: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    session: Mapped["Session"] = relationship(back_populates="task_traces")


# ─── Skills ──────────────────────────────────────────────────────────────────

class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    purpose: Mapped[str] = mapped_column(Text)
    trigger_conditions: Mapped[list] = mapped_column(JSON, default=list)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    tools_required: Mapped[list] = mapped_column(JSON, default=list)
    permissions_required: Mapped[list] = mapped_column(JSON, default=list)
    test_cases: Mapped[list] = mapped_column(JSON, default=list)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|active|deprecated|rolled_back
    project: Mapped[str | None] = mapped_column(String(128))
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    source_task_ids: Mapped[list | None] = mapped_column(JSON)
    meta: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now(), onupdate=func.now())

    versions: Mapped[list["SkillVersion"]] = relationship(back_populates="skill")
    runs: Mapped[list["SkillRun"]] = relationship(back_populates="skill")


class SkillVersion(Base):
    __tablename__ = "skill_versions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"))
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON)          # full skill dict at this version
    metrics_before: Mapped[dict | None] = mapped_column(JSON)
    metrics_after: Mapped[dict | None] = mapped_column(JSON)
    promoted_reason: Mapped[str | None] = mapped_column(Text)
    rolled_back_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    skill: Mapped["Skill"] = relationship(back_populates="versions")


class SkillRun(Base):
    __tablename__ = "skill_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"))
    skill_version: Mapped[int] = mapped_column(Integer)
    input_data: Mapped[dict | None] = mapped_column(JSON)
    output_data: Mapped[dict | None] = mapped_column(JSON)
    outcome: Mapped[str | None] = mapped_column(String(32))  # success|failure|error
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    skill: Mapped["Skill"] = relationship(back_populates="runs")


# ─── Reflections & Improvements ──────────────────────────────────────────────

class Reflection(Base):
    __tablename__ = "reflections"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project: Mapped[str | None] = mapped_column(String(128))
    trigger: Mapped[str] = mapped_column(String(64))      # manual|scheduled|error|outcome
    observations: Mapped[list] = mapped_column(JSON, default=list)
    lessons: Mapped[list] = mapped_column(JSON, default=list)
    proposed_improvements: Mapped[list] = mapped_column(JSON, default=list)
    session_id: Mapped[str | None] = mapped_column(String(64))
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())


class ImprovementProposal(Base):
    __tablename__ = "improvement_proposals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    reflection_id: Mapped[str | None] = mapped_column(ForeignKey("reflections.id"))
    improvement_type: Mapped[str] = mapped_column(String(64))  # skill_update|memory_policy|retrieval|context|etc
    title: Mapped[str] = mapped_column(String(256))
    reason: Mapped[str] = mapped_column(Text)
    current_behavior: Mapped[str] = mapped_column(Text)
    proposed_behavior: Mapped[str] = mapped_column(Text)
    risk: Mapped[str] = mapped_column(String(16), default="low")
    expected_benefit: Mapped[str] = mapped_column(Text)
    test_result: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="proposed")
    project: Mapped[str | None] = mapped_column(String(128))
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    meta: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now(), onupdate=func.now())

    approval: Mapped["ApprovalRequest | None"] = relationship(back_populates="improvement")


# ─── Approvals ───────────────────────────────────────────────────────────────

class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    improvement_id: Mapped[str | None] = mapped_column(ForeignKey("improvement_proposals.id"))
    title: Mapped[str] = mapped_column(String(256))
    request_type: Mapped[str] = mapped_column(String(64))
    summary: Mapped[dict] = mapped_column(JSON)            # full approval card
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|rejected|expired
    notification_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewer_note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    improvement: Mapped["ImprovementProposal | None"] = relationship(back_populates="approval")


# ─── Approval Audit Log ──────────────────────────────────────────────────────

class ApprovalAuditLog(Base):
    __tablename__ = "approval_audit_log"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    approval_id: Mapped[str] = mapped_column(String(64), index=True)
    decision: Mapped[str] = mapped_column(String(20))           # approved|rejected|expired
    actor: Mapped[str | None] = mapped_column(String(128))
    actor_user_id: Mapped[str | None] = mapped_column(String(64))
    actor_display_name: Mapped[str | None] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(32))             # dashboard|slack|pwa|api
    previous_status: Mapped[str] = mapped_column(String(20))
    new_status: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())


# ─── Rollbacks ───────────────────────────────────────────────────────────────

class Rollback(Base):
    __tablename__ = "rollbacks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    target_type: Mapped[str] = mapped_column(String(32))  # skill|policy|config
    target_id: Mapped[str] = mapped_column(String(64))
    from_version: Mapped[int | None] = mapped_column(Integer)
    to_version: Mapped[int | None] = mapped_column(Integer)
    metrics_before: Mapped[dict | None] = mapped_column(JSON)
    metrics_after: Mapped[dict | None] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    automatic: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())


# ─── Notifications ───────────────────────────────────────────────────────────

class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel: Mapped[str] = mapped_column(String(32))      # pwa|slack|dashboard
    title: Mapped[str] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text)
    approval_id: Mapped[str | None] = mapped_column(String(64))
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|sent|failed|read
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    endpoint: Mapped[str] = mapped_column(Text)
    keys: Mapped[dict] = mapped_column(JSON)
    user_agent: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())


# ─── Metrics ─────────────────────────────────────────────────────────────────

class MetricRecord(Base):
    __tablename__ = "metrics"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    value: Mapped[float] = mapped_column(Float)
    project: Mapped[str | None] = mapped_column(String(128))
    period: Mapped[str | None] = mapped_column(String(32))  # daily|weekly|session
    meta: Mapped[dict | None] = mapped_column(JSON)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    __table_args__ = (Index("ix_metrics_name_project", "name", "project"),)


# ─── Context Builds & Retrieval Logs ─────────────────────────────────────────

class ContextBuild(Base):
    __tablename__ = "context_builds"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    query: Mapped[str] = mapped_column(Text)
    session_id: Mapped[str | None] = mapped_column(String(64))
    project: Mapped[str | None] = mapped_column(String(128))
    memory_ids: Mapped[list] = mapped_column(JSON, default=list)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    budget_used: Mapped[float] = mapped_column(Float, default=0.0)
    relevance_scores: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())


class RetrievalLog(Base):
    __tablename__ = "retrieval_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    query: Mapped[str] = mapped_column(Text)
    layer: Mapped[str | None] = mapped_column(String(20))
    results_count: Mapped[int] = mapped_column(Integer, default=0)
    top_score: Mapped[float | None] = mapped_column(Float)
    session_id: Mapped[str | None] = mapped_column(String(64))
    project: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())


# ─── Episodic Chains ──────────────────────────────────────────────────────────

class EpisodicChain(Base):
    """Narrative episode linking a sequence of related memories into a coherent story."""

    __tablename__ = "episodic_chains"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(256))
    episode_summary: Mapped[str | None] = mapped_column(Text)
    episode_type: Mapped[str] = mapped_column(String(64), default="incident")
    # e.g.: incident|workflow|learning|deployment|conversation
    linked_memory_ids: Mapped[list] = mapped_column(JSON, default=list)
    procedural_lesson: Mapped[str | None] = mapped_column(Text)
    project: Mapped[str | None] = mapped_column(String(128))
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    meta: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_episodic_chains_project", "project"),
    )


# ─── Lifecycle Events ─────────────────────────────────────────────────────────

class LifecycleEvent(Base):
    """Audit log for memory lifecycle state changes."""

    __tablename__ = "lifecycle_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    memory_id: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    # memory_aged | memory_archived | memory_superseded | trust_increased |
    # trust_decreased | episodic_chain_built | consolidation_merge | verification_decayed
    from_state: Mapped[str | None] = mapped_column(String(20))
    to_state: Mapped[str | None] = mapped_column(String(20))
    trust_before: Mapped[float | None] = mapped_column(Float)
    trust_after: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(String(256))
    meta: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_lifecycle_events_memory", "memory_id"),
        Index("ix_lifecycle_events_type", "event_type"),
    )


# ─── Retrieval Sessions ───────────────────────────────────────────────────────

class RetrievalSession(Base):
    """Tracks a single orchestrated retrieval event for causal attribution and telemetry."""

    __tablename__ = "retrieval_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    query: Mapped[str] = mapped_column(Text)
    session_id: Mapped[str | None] = mapped_column(String(64))
    project: Mapped[str | None] = mapped_column(String(128))
    user_id: Mapped[str | None] = mapped_column(String(64))
    retrieved_memory_ids: Mapped[list] = mapped_column(JSON, default=list)
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    token_cost: Mapped[int] = mapped_column(Integer, default=0)

    # Outcome — set after task completes
    task_outcome: Mapped[str | None] = mapped_column(String(32))
    # success | failure | partial | None (unknown)
    rollback_id: Mapped[str | None] = mapped_column(String(64))
    has_correction: Mapped[bool] = mapped_column(Boolean, default=False)
    has_harmful_outcome: Mapped[bool] = mapped_column(Boolean, default=False)
    inference_applied: Mapped[bool] = mapped_column(Boolean, default=False)

    # Quality scores (computed at outcome time or at recall time)
    relevance_score: Mapped[float | None] = mapped_column(Float)
    usefulness_score: Mapped[float | None] = mapped_column(Float)
    harmfulness_score: Mapped[float | None] = mapped_column(Float)
    agreement_score: Mapped[float | None] = mapped_column(Float)
    token_efficiency_score: Mapped[float | None] = mapped_column(Float)

    # P10 adaptive retrieval fields
    task_category: Mapped[str | None] = mapped_column(String(32))
    # identity | procedural | troubleshooting | project_continuity | configuration | general
    active_providers: Mapped[list | None] = mapped_column(JSON)       # provider names that returned results
    provider_contributions: Mapped[dict | None] = mapped_column(JSON) # {provider_name: memories_count}
    retrieval_confidence_score: Mapped[float | None] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_retrieval_sessions_project", "project"),
        Index("ix_retrieval_sessions_session", "session_id"),
        Index("ix_retrieval_sessions_outcome", "task_outcome"),
        Index("ix_retrieval_sessions_inference", "inference_applied"),
        Index("ix_retrieval_sessions_category", "task_category"),
    )


# ─── Telemetry Snapshots ──────────────────────────────────────────────────────

class TelemetrySnapshot(Base):
    """Persisted time-series telemetry metric value."""

    __tablename__ = "telemetry_snapshots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    metric_name: Mapped[str] = mapped_column(String(128))
    metric_value: Mapped[float] = mapped_column(Float)
    period: Mapped[str] = mapped_column(String(32), default="daily")
    # hourly | daily | weekly
    project: Mapped[str | None] = mapped_column(String(128))
    meta: Mapped[dict | None] = mapped_column(JSON)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_telemetry_name_project", "metric_name", "project"),
        Index("ix_telemetry_recorded_at", "recorded_at"),
    )


# ─── Retrieval Feedback ───────────────────────────────────────────────────────

class RetrievalFeedback(Base):
    """Explicit outcome signal after a memory is used — drives trust evolution."""

    __tablename__ = "retrieval_feedback"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    memory_id: Mapped[str] = mapped_column(String(64), index=True)
    outcome: Mapped[str] = mapped_column(String(32))
    # success | failure | irrelevant | harmful
    reason: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_retrieval_feedback_memory", "memory_id"),
        Index("ix_retrieval_feedback_outcome", "outcome"),
    )


# ─── Graph Memory (P11) ──────────────────────────────────────────────────────

class GraphNode(Base):
    """A vertex in the Mimir knowledge graph representing any entity."""

    __tablename__ = "graph_nodes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    node_type: Mapped[str] = mapped_column(String(32))
    # user | project | memory | episodic_chain | procedure | retrieval_session |
    # improvement | task | environment | tool
    entity_id: Mapped[str] = mapped_column(String(128))   # ID of represented entity
    label: Mapped[str] = mapped_column(String(256))
    project: Mapped[str | None] = mapped_column(String(128))
    user_id: Mapped[str | None] = mapped_column(String(64))
    meta: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    edges_from: Mapped[list["GraphEdge"]] = relationship(
        foreign_keys="GraphEdge.source_node_id", back_populates="source_node"
    )
    edges_to: Mapped[list["GraphEdge"]] = relationship(
        foreign_keys="GraphEdge.target_node_id", back_populates="target_node"
    )

    __table_args__ = (
        Index("ix_graph_nodes_entity", "entity_id", "node_type", unique=True),
        Index("ix_graph_nodes_project", "project"),
        Index("ix_graph_nodes_type", "node_type"),
    )


class GraphEdge(Base):
    """A directed edge in the Mimir knowledge graph representing a relationship."""

    __tablename__ = "graph_edges"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_node_id: Mapped[str] = mapped_column(ForeignKey("graph_nodes.id"))
    target_node_id: Mapped[str] = mapped_column(ForeignKey("graph_nodes.id"))
    rel_type: Mapped[str] = mapped_column(String(32))
    # RELATED_TO | CAUSED_BY | SUPERSEDES | CONTRADICTS | DERIVED_FROM | USED_IN |
    # LED_TO | FAILED_BECAUSE_OF | RECOVERED_BY | DEPENDS_ON | PART_OF | REFERENCES
    confidence: Mapped[float] = mapped_column(Float, default=0.7, server_default="0.7")
    strength: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")
    source: Mapped[str] = mapped_column(String(64), default="auto")
    # auto_episodic | auto_rollback | auto_supersession | auto_retrieval | auto_improvement |
    # auto_contradiction | manual
    verification_status: Mapped[str] = mapped_column(
        String(32), default="inferred", server_default="inferred"
    )
    # inferred | confirmed | rejected
    meta: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    source_node: Mapped["GraphNode"] = relationship(
        foreign_keys=[source_node_id], back_populates="edges_from"
    )
    target_node: Mapped["GraphNode"] = relationship(
        foreign_keys=[target_node_id], back_populates="edges_to"
    )

    __table_args__ = (
        Index("ix_graph_edges_source", "source_node_id"),
        Index("ix_graph_edges_target", "target_node_id"),
        Index("ix_graph_edges_rel", "rel_type"),
        Index("ix_graph_edges_src_tgt_rel", "source_node_id", "target_node_id", "rel_type", unique=True),
    )


# ─── Provider Stats (P10) ─────────────────────────────────────────────────────

class ProviderStats(Base):
    """Per-provider usefulness and drift metrics for adaptive retrieval weighting."""

    __tablename__ = "provider_stats"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider_name: Mapped[str] = mapped_column(String(32))
    # vector | keyword | identity | episodic_recent | procedural | high_trust
    project: Mapped[str | None] = mapped_column(String(128))    # None = global
    task_category: Mapped[str | None] = mapped_column(String(32))  # None = aggregate all

    # Accumulation counters
    total_sessions: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    useful_sessions: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    harmful_sessions: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_memories_contributed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Computed rates (updated by worker after each aggregation pass)
    usefulness_rate: Mapped[float] = mapped_column(Float, default=0.5, server_default="0.5")
    harmful_rate: Mapped[float] = mapped_column(Float, default=0.0, server_default="0.0")
    avg_agreement_contribution: Mapped[float] = mapped_column(Float, default=0.5, server_default="0.5")
    avg_token_efficiency: Mapped[float] = mapped_column(Float, default=0.5, server_default="0.5")

    # Adaptive weight (bounded; updated conservatively by worker)
    weight_current: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")

    # Drift tracking
    drift_flagged: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    drift_reason: Mapped[str | None] = mapped_column(String(256))
    drift_detected_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    last_updated_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_provider_stats_name", "provider_name"),
        Index("ix_provider_stats_project", "project"),
        Index("ix_provider_stats_category", "task_category"),
        Index("ix_provider_stats_name_project_cat", "provider_name", "project", "task_category"),
    )


# ─── Simulation / Predictive Planning (P12) ──────────────────────────────────

class SimulationPlan(Base):
    """A structured execution plan with goals, steps, dependencies, and risk estimates."""

    __tablename__ = "simulation_plans"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(32), default="draft"
    )
    # draft | pending_approval | approved | rejected | executed | cancelled
    steps: Mapped[list] = mapped_column(JSON, default=list)
    # list of {id, description, dependencies, required_procedures, risk_estimate, rollback_option}
    risk_estimate: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_estimate: Mapped[float] = mapped_column(Float, default=0.5)
    rollback_options: Mapped[list] = mapped_column(JSON, default=list)
    expected_outcomes: Mapped[list] = mapped_column(JSON, default=list)
    approval_required: Mapped[bool] = mapped_column(Boolean, default=False)
    approval_id: Mapped[str | None] = mapped_column(String(64))
    graph_valid: Mapped[bool] = mapped_column(Boolean, default=True)
    graph_errors: Mapped[list | None] = mapped_column(JSON)
    project: Mapped[str | None] = mapped_column(String(128))
    user_id: Mapped[str | None] = mapped_column(String(64))
    meta: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    simulation_runs: Mapped[list["SimulationRun"]] = relationship(back_populates="plan")

    __table_args__ = (
        Index("ix_simulation_plans_project", "project"),
        Index("ix_simulation_plans_status", "status"),
    )


class SimulationRun(Base):
    """Result of a simulation (full, counterfactual, or risk-only) for a plan."""

    __tablename__ = "simulation_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("simulation_plans.id"))
    simulation_type: Mapped[str] = mapped_column(String(32), default="full")
    # full | counterfactual | risk_only
    counterfactual_description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="complete")
    # pending | complete | failed
    paths: Mapped[list] = mapped_column(JSON, default=list)
    best_path_id: Mapped[str | None] = mapped_column(String(128))
    success_probability: Mapped[float] = mapped_column(Float, default=0.5)
    risk_score: Mapped[float] = mapped_column(Float, default=0.5)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.1)
    expected_failure_modes: Mapped[list | None] = mapped_column(JSON)
    historical_memories_used: Mapped[list | None] = mapped_column(JSON)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    depth_reached: Mapped[int] = mapped_column(Integer, default=0)
    # Outcome tracking — filled in after plan executes
    actual_outcome: Mapped[str | None] = mapped_column(String(32))
    # success | failure | partial | cancelled
    forecast_was_correct: Mapped[bool | None] = mapped_column(Boolean)
    project: Mapped[str | None] = mapped_column(String(128))
    user_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    plan: Mapped["SimulationPlan"] = relationship(back_populates="simulation_runs")

    __table_args__ = (
        Index("ix_simulation_runs_plan", "plan_id"),
        Index("ix_simulation_runs_type", "simulation_type"),
        Index("ix_simulation_runs_project", "project"),
    )


class ForecastCalibration(Base):
    """Tracks forecast accuracy for calibration and overconfidence detection."""

    __tablename__ = "forecast_calibration"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project: Mapped[str | None] = mapped_column(String(128))
    period: Mapped[str] = mapped_column(String(32), default="daily")
    total_forecasts: Mapped[int] = mapped_column(Integer, default=0)
    correct_forecasts: Mapped[int] = mapped_column(Integer, default=0)
    forecast_accuracy: Mapped[float] = mapped_column(Float, default=0.0)
    overconfidence_rate: Mapped[float] = mapped_column(Float, default=0.0)
    underconfidence_rate: Mapped[float] = mapped_column(Float, default=0.0)
    mean_prediction_error: Mapped[float] = mapped_column(Float, default=0.0)
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_forecast_calibration_project", "project"),
        Index("ix_forecast_calibration_period", "period"),
    )


# ─── Job Locks ────────────────────────────────────────────────────────────────

class JobLock(Base):
    """DB-backed distributed lock for multi-worker job coordination."""

    __tablename__ = "job_locks"

    job_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    locked_by: Mapped[str] = mapped_column(String(256))  # worker hostname / PID
    locked_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(32), default="locked")  # locked | released

    __table_args__ = (
        Index("ix_job_locks_status", "status"),
        Index("ix_job_locks_expires", "expires_at"),
    )


# ─── OAuth 2.1 / PKCE ─────────────────────────────────────────────────────────

class OAuthClient(Base):
    """Dynamically registered OAuth client (RFC 7591)."""

    __tablename__ = "oauth_clients"

    client_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    client_name: Mapped[str | None] = mapped_column(String(256))
    redirect_uris: Mapped[str] = mapped_column(Text)           # JSON array
    grant_types: Mapped[str] = mapped_column(Text, default='["authorization_code","refresh_token"]')
    response_types: Mapped[str] = mapped_column(Text, default='["code"]')
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)  # PKCE clients are public
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())


class OAuthAuthorizationCode(Base):
    """Short-lived authorization code exchanged for tokens."""

    __tablename__ = "oauth_authorization_codes"

    code: Mapped[str] = mapped_column(String(128), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(128), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    redirect_uri: Mapped[str] = mapped_column(Text)
    scope: Mapped[str | None] = mapped_column(String(512))
    code_challenge: Mapped[str] = mapped_column(String(256))
    code_challenge_method: Mapped[str] = mapped_column(String(10), default="S256")
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())


class OAuthToken(Base):
    """Access token (stored hashed; raw token shown once to client)."""

    __tablename__ = "oauth_tokens"

    token_hash: Mapped[str] = mapped_column(String(128), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(128), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    scope: Mapped[str | None] = mapped_column(String(512))
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())


class OAuthRefreshToken(Base):
    """Refresh token (stored hashed; rotated on use)."""

    __tablename__ = "oauth_refresh_tokens"

    token_hash: Mapped[str] = mapped_column(String(128), primary_key=True)
    access_token_hash: Mapped[str | None] = mapped_column(String(128), index=True)
    client_id: Mapped[str] = mapped_column(String(128), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    scope: Mapped[str | None] = mapped_column(String(512))
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
