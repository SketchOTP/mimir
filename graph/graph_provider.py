"""SQLite-backed graph provider — provider-agnostic interface over graph_nodes/graph_edges tables."""

from __future__ import annotations

import uuid
from datetime import datetime, UTC

from sqlalchemy import select, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from graph.memory_graph import GraphEdge, GraphNode, REL_TYPES, NODE_TYPES
from storage.models import GraphNode as GraphNodeORM, GraphEdge as GraphEdgeORM


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _orm_to_node(row: GraphNodeORM) -> GraphNode:
    return GraphNode(
        id=row.id,
        node_type=row.node_type,
        entity_id=row.entity_id,
        label=row.label,
        project=row.project,
        user_id=row.user_id,
        meta=row.meta,
        created_at=row.created_at,
    )


def _orm_to_edge(row: GraphEdgeORM) -> GraphEdge:
    return GraphEdge(
        id=row.id,
        source_node_id=row.source_node_id,
        target_node_id=row.target_node_id,
        rel_type=row.rel_type,
        confidence=row.confidence,
        strength=row.strength,
        source=row.source,
        verification_status=row.verification_status,
        meta=row.meta,
        created_at=row.created_at,
    )


# ── Node operations ────────────────────────────────────────────────────────────

async def get_node_by_entity(
    session: AsyncSession,
    entity_id: str,
    node_type: str,
) -> GraphNode | None:
    stmt = select(GraphNodeORM).where(
        GraphNodeORM.entity_id == entity_id,
        GraphNodeORM.node_type == node_type,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    return _orm_to_node(row) if row else None


async def get_or_create_node(
    session: AsyncSession,
    entity_id: str,
    node_type: str,
    label: str,
    *,
    project: str | None = None,
    user_id: str | None = None,
    meta: dict | None = None,
) -> GraphNode:
    if node_type not in NODE_TYPES:
        raise ValueError(f"Unknown node_type: {node_type!r}")

    existing = await get_node_by_entity(session, entity_id, node_type)
    if existing:
        return existing

    row = GraphNodeORM(
        id=str(uuid.uuid4()),
        node_type=node_type,
        entity_id=entity_id,
        label=label[:256],
        project=project,
        user_id=user_id,
        meta=meta,
        created_at=_now(),
    )
    session.add(row)
    await session.flush()
    return _orm_to_node(row)


async def get_node_by_id(session: AsyncSession, node_id: str) -> GraphNode | None:
    row = await session.get(GraphNodeORM, node_id)
    return _orm_to_node(row) if row else None


# ── Edge operations ────────────────────────────────────────────────────────────

async def get_edge(
    session: AsyncSession,
    source_node_id: str,
    target_node_id: str,
    rel_type: str,
) -> GraphEdge | None:
    stmt = select(GraphEdgeORM).where(
        GraphEdgeORM.source_node_id == source_node_id,
        GraphEdgeORM.target_node_id == target_node_id,
        GraphEdgeORM.rel_type == rel_type,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    return _orm_to_edge(row) if row else None


async def get_or_create_edge(
    session: AsyncSession,
    source_node_id: str,
    target_node_id: str,
    rel_type: str,
    *,
    confidence: float = 0.7,
    strength: float = 1.0,
    source: str = "auto",
    verification_status: str = "inferred",
    meta: dict | None = None,
) -> GraphEdge:
    if rel_type not in REL_TYPES:
        raise ValueError(f"Unknown rel_type: {rel_type!r}")

    existing = await get_edge(session, source_node_id, target_node_id, rel_type)
    if existing:
        return existing

    row = GraphEdgeORM(
        id=str(uuid.uuid4()),
        source_node_id=source_node_id,
        target_node_id=target_node_id,
        rel_type=rel_type,
        confidence=max(0.0, min(1.0, confidence)),
        strength=max(0.0, min(1.0, strength)),
        source=source,
        verification_status=verification_status,
        meta=meta,
        created_at=_now(),
    )
    session.add(row)
    await session.flush()
    return _orm_to_edge(row)


async def get_neighbors(
    session: AsyncSession,
    node_id: str,
    *,
    rel_types: list[str] | None = None,
    direction: str = "both",    # "out" | "in" | "both"
    min_confidence: float = 0.0,
    limit: int = 50,
) -> list[tuple[GraphNode, GraphEdge]]:
    """Return (neighbor_node, edge) pairs from node_id, bounded by limit."""
    conditions = [GraphEdgeORM.confidence >= min_confidence]

    if rel_types:
        conditions.append(GraphEdgeORM.rel_type.in_(rel_types))

    if direction == "out":
        conditions.append(GraphEdgeORM.source_node_id == node_id)
        stmt = (
            select(GraphNodeORM, GraphEdgeORM)
            .join(GraphEdgeORM, GraphNodeORM.id == GraphEdgeORM.target_node_id)
            .where(and_(*conditions))
            .limit(limit)
        )
    elif direction == "in":
        conditions.append(GraphEdgeORM.target_node_id == node_id)
        stmt = (
            select(GraphNodeORM, GraphEdgeORM)
            .join(GraphEdgeORM, GraphNodeORM.id == GraphEdgeORM.source_node_id)
            .where(and_(*conditions))
            .limit(limit)
        )
    else:
        # both directions — union approach via two separate queries
        out_cond = [GraphEdgeORM.source_node_id == node_id] + [GraphEdgeORM.confidence >= min_confidence]
        in_cond = [GraphEdgeORM.target_node_id == node_id] + [GraphEdgeORM.confidence >= min_confidence]
        if rel_types:
            out_cond.append(GraphEdgeORM.rel_type.in_(rel_types))
            in_cond.append(GraphEdgeORM.rel_type.in_(rel_types))

        out_stmt = (
            select(GraphNodeORM, GraphEdgeORM)
            .join(GraphEdgeORM, GraphNodeORM.id == GraphEdgeORM.target_node_id)
            .where(and_(*out_cond))
            .limit(limit)
        )
        in_stmt = (
            select(GraphNodeORM, GraphEdgeORM)
            .join(GraphEdgeORM, GraphNodeORM.id == GraphEdgeORM.source_node_id)
            .where(and_(*in_cond))
            .limit(limit)
        )
        out_rows = (await session.execute(out_stmt)).all()
        in_rows = (await session.execute(in_stmt)).all()
        all_rows = out_rows + in_rows
        return [(_orm_to_node(n), _orm_to_edge(e)) for n, e in all_rows[:limit]]

    rows = (await session.execute(stmt)).all()
    return [(_orm_to_node(n), _orm_to_edge(e)) for n, e in rows]


async def count_node_degree(session: AsyncSession, node_id: str) -> int:
    """Total edges (in + out) for a node."""
    out_count = (await session.execute(
        select(func.count()).where(GraphEdgeORM.source_node_id == node_id)
    )).scalar_one()
    in_count = (await session.execute(
        select(func.count()).where(GraphEdgeORM.target_node_id == node_id)
    )).scalar_one()
    return (out_count or 0) + (in_count or 0)
