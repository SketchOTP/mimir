"""Core data structures for the Mimir graph memory layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# ── Valid node types ───────────────────────────────────────────────────────────

NODE_TYPES = {
    "user", "project", "memory", "episodic_chain", "procedure",
    "retrieval_session", "improvement", "task", "environment", "tool",
    "plan", "simulation",
}

# ── Valid relationship types ───────────────────────────────────────────────────

REL_TYPES = {
    "RELATED_TO",
    "CAUSED_BY",
    "SUPERSEDES",
    "CONTRADICTS",
    "DERIVED_FROM",
    "USED_IN",
    "LED_TO",
    "FAILED_BECAUSE_OF",
    "RECOVERED_BY",
    "DEPENDS_ON",
    "PART_OF",
    "REFERENCES",
    "SIMULATED",
    "PREDICTED",
    "ACTUALIZED_AS",
}


@dataclass
class GraphNode:
    id: str
    node_type: str          # one of NODE_TYPES
    entity_id: str          # ID of the represented entity (memory_id, session_id, etc.)
    label: str
    project: str | None = None
    user_id: str | None = None
    meta: dict[str, Any] | None = None
    created_at: datetime | None = None


@dataclass
class GraphEdge:
    id: str
    source_node_id: str
    target_node_id: str
    rel_type: str           # one of REL_TYPES
    confidence: float = 0.7
    strength: float = 1.0
    source: str = "auto"    # "auto_episodic" | "auto_rollback" | "auto_supersession" | "manual" …
    verification_status: str = "inferred"   # "inferred" | "confirmed" | "rejected"
    meta: dict[str, Any] | None = None
    created_at: datetime | None = None


@dataclass
class GraphPath:
    """A sequence of (node, edge) pairs representing a traversal path."""
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)

    @property
    def length(self) -> int:
        return len(self.edges)

    @property
    def min_confidence(self) -> float:
        if not self.edges:
            return 1.0
        return min(e.confidence for e in self.edges)


@dataclass
class GraphTelemetry:
    total_nodes: int = 0
    total_edges: int = 0
    nodes_by_type: dict[str, int] = field(default_factory=dict)
    edges_by_rel: dict[str, int] = field(default_factory=dict)
    most_connected: list[dict] = field(default_factory=list)   # [{entity_id, label, degree}]
    high_risk_chains: list[dict] = field(default_factory=list)
