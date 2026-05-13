"""P11 Graph Memory API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import UserContext, get_current_user
from storage.database import get_session
from graph.graph_builder import run_graph_build_pass
from graph.graph_provider import get_node_by_entity, get_neighbors, get_node_by_id
from graph.graph_queries import (
    compute_graph_telemetry,
    find_causal_chains,
    find_contradictions,
    get_most_connected_nodes,
    traverse_related,
)

router = APIRouter(prefix="/graph", tags=["graph"])


def _node_dict(node) -> dict[str, Any]:
    return {
        "id": node.id,
        "node_type": node.node_type,
        "entity_id": node.entity_id,
        "label": node.label,
        "project": node.project,
        "created_at": node.created_at.isoformat() if node.created_at else None,
    }


def _edge_dict(edge) -> dict[str, Any]:
    return {
        "id": edge.id,
        "source_node_id": edge.source_node_id,
        "target_node_id": edge.target_node_id,
        "rel_type": edge.rel_type,
        "confidence": edge.confidence,
        "strength": edge.strength,
        "source": edge.source,
        "verification_status": edge.verification_status,
    }


# ── Node lookup ────────────────────────────────────────────────────────────────

@router.get("/nodes/{entity_id}")
async def get_graph_node(
    entity_id: str,
    node_type: str = Query(default="memory"),
    db: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    node = await get_node_by_entity(db, entity_id, node_type)
    if node is None:
        raise HTTPException(status_code=404, detail="Graph node not found")
    neighbors = await get_neighbors(db, node.id, direction="both", limit=50)
    return {
        "node": _node_dict(node),
        "edges": [
            {"neighbor": _node_dict(n), "edge": _edge_dict(e)}
            for n, e in neighbors
        ],
    }


# ── Multi-hop traversal ────────────────────────────────────────────────────────

@router.get("/traverse/{entity_id}")
async def traverse_from_entity(
    entity_id: str,
    node_type: str = Query(default="memory"),
    max_depth: int = Query(default=3, ge=1, le=5),
    max_nodes: int = Query(default=20, ge=1, le=50),
    min_confidence: float = Query(default=0.5, ge=0.0, le=1.0),
    db: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    node = await get_node_by_entity(db, entity_id, node_type)
    if node is None:
        raise HTTPException(status_code=404, detail="Graph node not found")
    results = await traverse_related(
        db,
        node.id,
        max_depth=max_depth,
        max_nodes=max_nodes,
        min_confidence=min_confidence,
    )
    return {
        "start": _node_dict(node),
        "traversal": [
            {"node": _node_dict(n), "depth": depth}
            for n, depth in results
        ],
        "count": len(results),
    }


# ── Causal chain explorer ──────────────────────────────────────────────────────

@router.get("/causal/{entity_id}")
async def causal_chain_from_entity(
    entity_id: str,
    node_type: str = Query(default="memory"),
    max_depth: int = Query(default=5, ge=1, le=5),
    min_confidence: float = Query(default=0.5, ge=0.0, le=1.0),
    db: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    node = await get_node_by_entity(db, entity_id, node_type)
    if node is None:
        raise HTTPException(status_code=404, detail="Graph node not found")
    paths = await find_causal_chains(db, node.id, max_depth=max_depth, min_confidence=min_confidence)
    return {
        "start": _node_dict(node),
        "chains": [
            {
                "length": path.length,
                "min_confidence": path.min_confidence,
                "nodes": [_node_dict(n) for n in path.nodes],
                "edges": [_edge_dict(e) for e in path.edges],
            }
            for path in paths
        ],
        "count": len(paths),
    }


# ── Contradiction graph ────────────────────────────────────────────────────────

@router.get("/contradictions/{entity_id}")
async def contradictions_for_entity(
    entity_id: str,
    node_type: str = Query(default="memory"),
    db: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    node = await get_node_by_entity(db, entity_id, node_type)
    if node is None:
        raise HTTPException(status_code=404, detail="Graph node not found")
    contradictions = await find_contradictions(db, node.id)
    return {
        "node": _node_dict(node),
        "contradictions": [
            {"neighbor": _node_dict(n), "edge": _edge_dict(e)}
            for n, e in contradictions
        ],
        "count": len(contradictions),
    }


# ── Centrality / telemetry ─────────────────────────────────────────────────────

@router.get("/centrality")
async def graph_centrality(
    project: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
    db: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    nodes = await get_most_connected_nodes(db, project=project, limit=limit)
    return {"most_connected": nodes, "count": len(nodes)}


@router.get("/telemetry")
async def graph_telemetry(
    project: str | None = Query(default=None),
    db: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    telem = await compute_graph_telemetry(db, project=project)
    return {
        "total_nodes": telem.total_nodes,
        "total_edges": telem.total_edges,
        "nodes_by_type": telem.nodes_by_type,
        "edges_by_rel": telem.edges_by_rel,
        "most_connected": telem.most_connected,
        "high_risk_chains": telem.high_risk_chains,
    }


# ── Manual graph build trigger ────────────────────────────────────────────────

@router.post("/build")
async def trigger_graph_build(
    db: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    result = await run_graph_build_pass(db)
    return result
