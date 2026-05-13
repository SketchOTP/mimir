"""Bounded graph traversal queries: multi-hop, causal chains, centrality, contradictions, boost."""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from graph.memory_graph import GraphEdge, GraphNode, GraphPath, GraphTelemetry
from graph.graph_provider import (
    get_node_by_entity,
    get_node_by_id,
    get_neighbors,
)
from storage.models import GraphNode as GraphNodeORM, GraphEdge as GraphEdgeORM

logger = logging.getLogger(__name__)

# Safety limits — prevent runaway traversal
MAX_GRAPH_DEPTH = 5
MAX_GRAPH_NODES = 50
MAX_CAUSAL_DEPTH = 5
MAX_BOOST_PATHS = 10
_BOOST_CONFIDENCE_THRESHOLD = 0.70    # only high-confidence edges count for boost
_MAX_GRAPH_BOOST = 0.20               # max additive boost on composite score


# ── Multi-hop traversal ────────────────────────────────────────────────────────

async def traverse_related(
    session: AsyncSession,
    start_node_id: str,
    *,
    max_depth: int = 3,
    max_nodes: int = 20,
    rel_types: list[str] | None = None,
    min_confidence: float = 0.5,
) -> list[tuple[GraphNode, int]]:
    """BFS from start_node_id. Returns [(node, depth)] bounded by limits.

    Safety: max_depth capped to MAX_GRAPH_DEPTH, max_nodes to MAX_GRAPH_NODES.
    """
    max_depth = min(max_depth, MAX_GRAPH_DEPTH)
    max_nodes = min(max_nodes, MAX_GRAPH_NODES)

    visited: set[str] = {start_node_id}
    result: list[tuple[GraphNode, int]] = []
    queue: deque[tuple[str, int]] = deque([(start_node_id, 0)])

    while queue and len(result) < max_nodes:
        current_id, depth = queue.popleft()
        if depth >= max_depth:
            continue

        neighbors = await get_neighbors(
            session,
            current_id,
            rel_types=rel_types,
            direction="both",
            min_confidence=min_confidence,
            limit=MAX_GRAPH_NODES,
        )
        for node, edge in neighbors:
            if node.id not in visited and len(result) < max_nodes:
                visited.add(node.id)
                result.append((node, depth + 1))
                queue.append((node.id, depth + 1))

    return result


# ── Causal chain construction ──────────────────────────────────────────────────

_CAUSAL_REL_TYPES = {"CAUSED_BY", "LED_TO", "FAILED_BECAUSE_OF", "RECOVERED_BY"}


async def find_causal_chains(
    session: AsyncSession,
    root_node_id: str,
    *,
    max_depth: int = MAX_CAUSAL_DEPTH,
    min_confidence: float = 0.5,
) -> list[GraphPath]:
    """DFS from root following causal relationship types. Returns all causal paths found."""
    max_depth = min(max_depth, MAX_CAUSAL_DEPTH)
    paths: list[GraphPath] = []

    async def _dfs(node_id: str, current_path: GraphPath, visited: set[str], depth: int) -> None:
        if depth >= max_depth or len(paths) >= 20:
            return

        neighbors = await get_neighbors(
            session, node_id,
            rel_types=list(_CAUSAL_REL_TYPES),
            direction="out",
            min_confidence=min_confidence,
            limit=10,
        )
        for node, edge in neighbors:
            if node.id in visited:
                continue
            new_path = GraphPath(
                nodes=current_path.nodes + [node],
                edges=current_path.edges + [edge],
            )
            if len(new_path.nodes) > 1:
                paths.append(new_path)
            new_visited = visited | {node.id}
            await _dfs(node.id, new_path, new_visited, depth + 1)

    start_node = await get_node_by_id(session, root_node_id)
    if start_node is None:
        return []

    initial_path = GraphPath(nodes=[start_node], edges=[])
    await _dfs(root_node_id, initial_path, {root_node_id}, 0)
    return paths


# ── Contradiction graph ────────────────────────────────────────────────────────

async def find_contradictions(
    session: AsyncSession,
    node_id: str,
) -> list[tuple[GraphNode, GraphEdge]]:
    """Return nodes connected to node_id via CONTRADICTS or SUPERSEDES edges."""
    return await get_neighbors(
        session, node_id,
        rel_types=["CONTRADICTS", "SUPERSEDES"],
        direction="both",
        min_confidence=0.0,
    )


# ── Centrality / telemetry ─────────────────────────────────────────────────────

async def get_most_connected_nodes(
    session: AsyncSession,
    *,
    project: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return top-N nodes by total edge degree."""
    # Count out-degree
    out_sub = (
        select(GraphEdgeORM.source_node_id.label("node_id"), func.count().label("cnt"))
        .group_by(GraphEdgeORM.source_node_id)
        .subquery()
    )
    in_sub = (
        select(GraphEdgeORM.target_node_id.label("node_id"), func.count().label("cnt"))
        .group_by(GraphEdgeORM.target_node_id)
        .subquery()
    )

    node_q = select(GraphNodeORM)
    if project:
        node_q = node_q.where(GraphNodeORM.project == project)
    nodes = (await session.execute(node_q)).scalars().all()

    results = []
    for node in nodes:
        out_row = (await session.execute(
            select(func.count()).where(GraphEdgeORM.source_node_id == node.id)
        )).scalar_one() or 0
        in_row = (await session.execute(
            select(func.count()).where(GraphEdgeORM.target_node_id == node.id)
        )).scalar_one() or 0
        results.append({
            "node_id": node.id,
            "entity_id": node.entity_id,
            "node_type": node.node_type,
            "label": node.label,
            "degree": out_row + in_row,
            "project": node.project,
        })

    results.sort(key=lambda x: x["degree"], reverse=True)
    return results[:limit]


async def compute_graph_telemetry(
    session: AsyncSession,
    *,
    project: str | None = None,
) -> GraphTelemetry:
    node_q = select(GraphNodeORM)
    if project:
        node_q = node_q.where(GraphNodeORM.project == project)
    nodes = (await session.execute(node_q)).scalars().all()

    edge_q = select(GraphEdgeORM)
    edges = (await session.execute(edge_q)).scalars().all()

    nodes_by_type: dict[str, int] = {}
    for n in nodes:
        nodes_by_type[n.node_type] = nodes_by_type.get(n.node_type, 0) + 1

    edges_by_rel: dict[str, int] = {}
    for e in edges:
        edges_by_rel[e.rel_type] = edges_by_rel.get(e.rel_type, 0) + 1

    most_connected = await get_most_connected_nodes(session, project=project, limit=10)

    # High-risk causal chains: FAILED_BECAUSE_OF edges with confidence >= 0.7
    high_risk = [
        {"source": e.source_node_id, "target": e.target_node_id, "confidence": e.confidence}
        for e in edges
        if e.rel_type == "FAILED_BECAUSE_OF" and e.confidence >= 0.7
    ]

    return GraphTelemetry(
        total_nodes=len(nodes),
        total_edges=len(edges),
        nodes_by_type=nodes_by_type,
        edges_by_rel=edges_by_rel,
        most_connected=most_connected,
        high_risk_chains=high_risk,
    )


# ── Graph-assisted retrieval boost ────────────────────────────────────────────

async def compute_graph_boost(
    session: AsyncSession,
    memory_ids: list[str],
) -> dict[str, float]:
    """Return an additive boost [0, MAX_GRAPH_BOOST] for each memory_id.

    Boost is proportional to number of high-confidence incoming edges from other
    high-trust nodes (convergence). More convergent paths → stronger boost.
    Capped to prevent runaway score inflation.
    """
    if not memory_ids:
        return {}

    boosts: dict[str, float] = {}
    for mem_id in memory_ids:
        node = await get_node_by_entity(session, mem_id, "memory")
        if node is None:
            boosts[mem_id] = 0.0
            continue

        # Count incoming high-confidence edges
        in_count = (await session.execute(
            select(func.count()).where(
                GraphEdgeORM.target_node_id == node.id,
                GraphEdgeORM.confidence >= _BOOST_CONFIDENCE_THRESHOLD,
            )
        )).scalar_one() or 0

        # Convergence boost: saturates at 5 paths → max boost
        # boost = min(in_count / 5, 1.0) * MAX_GRAPH_BOOST
        boosts[mem_id] = min(in_count / 5.0, 1.0) * _MAX_GRAPH_BOOST

    return boosts
