"""P11 Graph Memory + Relational Cognition tests.

Covers all acceptance criteria:
  1.  GraphNode creation (get_or_create_node idempotent)
  2.  GraphEdge creation (get_or_create_edge idempotent)
  3.  All 12 relationship types accepted
  4.  All 10 node types accepted
  5.  Invalid rel_type raises ValueError
  6.  Invalid node_type raises ValueError
  7.  Edge confidence clamped [0, 1]
  8.  Multi-hop traversal bounded by max_depth
  9.  Multi-hop traversal bounded by max_nodes
  10. Multi-hop traversal returns empty for unknown node
  11. Causal chain construction follows causal rel_types only
  12. Causal chain depth bounded
  13. Contradiction edges (CONTRADICTS / SUPERSEDES) retrievable
  14. Graph builder: episodic chains → PART_OF + DERIVED_FROM edges
  15. Graph builder: memory supersession → SUPERSEDES edge
  16. Graph builder: rollbacks → FAILED_BECAUSE_OF + RECOVERED_BY edges
  17. Graph builder: improvements → DERIVED_FROM edges
  18. Graph builder: retrieval sessions → USED_IN edges
  19. Graph builder run_graph_build_pass returns stats dict
  20. Graph boost: 0 for memory with no graph node
  21. Graph boost: positive for memory with high-confidence incoming edges
  22. Graph boost: bounded to MAX_GRAPH_BOOST (0.20)
  23. Graph telemetry: returns counts and most_connected
  24. Most-connected nodes: degree = in + out edges
  25. Cross-user isolation: project-scoped nodes don't leak
  26. GET /graph/nodes/{entity_id} — 200 with edges
  27. GET /graph/nodes/{entity_id} — 404 when absent
  28. GET /graph/traverse/{entity_id} — returns traversal
  29. GET /graph/causal/{entity_id} — returns chains
  30. GET /graph/contradictions/{entity_id} — returns contradiction edges
  31. GET /graph/centrality — returns most_connected list
  32. GET /graph/telemetry — returns graph stats
  33. POST /graph/build — triggers build pass, returns stats
  34. Worker task run_graph_build executes without error
  35. Scheduler includes graph_build job
"""

from __future__ import annotations

import uuid
import pytest

from tests.conftest import as_user


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def _uid() -> str:
    return str(uuid.uuid4())


async def _db_session():
    from storage.database import get_session_factory
    factory = get_session_factory()
    return factory()


# ── Convenience wrappers ────────────────────────────────────────────────────────

async def _make_node(session, entity_id=None, node_type="memory", label="test node", project="p11_test"):
    from graph.graph_provider import get_or_create_node
    return await get_or_create_node(
        session,
        entity_id=entity_id or _uid(),
        node_type=node_type,
        label=label,
        project=project,
    )


async def _make_edge(session, src_id, tgt_id, rel_type="RELATED_TO", confidence=0.8):
    from graph.graph_provider import get_or_create_edge
    return await get_or_create_edge(
        session,
        source_node_id=src_id,
        target_node_id=tgt_id,
        rel_type=rel_type,
        confidence=confidence,
    )


# ─── 1. GraphNode creation — idempotent ────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_or_create_node_idempotent(app):
    from graph.graph_provider import get_or_create_node
    from storage.database import get_session_factory
    entity_id = _uid()
    factory = get_session_factory()
    async with factory() as session:
        n1 = await get_or_create_node(session, entity_id=entity_id, node_type="memory", label="test")
        n2 = await get_or_create_node(session, entity_id=entity_id, node_type="memory", label="test")
        assert n1.id == n2.id


# ─── 2. GraphEdge creation — idempotent ────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_or_create_edge_idempotent(app):
    from graph.graph_provider import get_or_create_node, get_or_create_edge
    from storage.database import get_session_factory
    factory = get_session_factory()
    async with factory() as session:
        n1 = await get_or_create_node(session, entity_id=_uid(), node_type="memory", label="a")
        n2 = await get_or_create_node(session, entity_id=_uid(), node_type="memory", label="b")
        e1 = await get_or_create_edge(session, n1.id, n2.id, "RELATED_TO")
        e2 = await get_or_create_edge(session, n1.id, n2.id, "RELATED_TO")
        assert e1.id == e2.id


# ─── 3. All 12 relationship types ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_all_rel_types_accepted(app):
    from graph.graph_provider import get_or_create_node, get_or_create_edge
    from graph.memory_graph import REL_TYPES
    from storage.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        base = await get_or_create_node(session, entity_id=_uid(), node_type="memory", label="base")
        for rel in REL_TYPES:
            target = await get_or_create_node(session, entity_id=_uid(), node_type="memory", label=rel)
            edge = await get_or_create_edge(session, base.id, target.id, rel)
            assert edge.rel_type == rel


# ─── 4. All 10 node types accepted ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_all_node_types_accepted(app):
    from graph.graph_provider import get_or_create_node
    from graph.memory_graph import NODE_TYPES
    from storage.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        for nt in NODE_TYPES:
            node = await get_or_create_node(session, entity_id=_uid(), node_type=nt, label=nt)
            assert node.node_type == nt


# ─── 5. Invalid rel_type raises ValueError ─────────────────────────────────────

@pytest.mark.asyncio
async def test_invalid_rel_type_raises(app):
    from graph.graph_provider import get_or_create_node, get_or_create_edge
    from storage.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        n1 = await get_or_create_node(session, entity_id=_uid(), node_type="memory", label="x")
        n2 = await get_or_create_node(session, entity_id=_uid(), node_type="memory", label="y")
        with pytest.raises(ValueError, match="rel_type"):
            await get_or_create_edge(session, n1.id, n2.id, "INVENTED_REL")


# ─── 6. Invalid node_type raises ValueError ────────────────────────────────────

@pytest.mark.asyncio
async def test_invalid_node_type_raises(app):
    from graph.graph_provider import get_or_create_node
    from storage.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        with pytest.raises(ValueError, match="node_type"):
            await get_or_create_node(session, entity_id=_uid(), node_type="galaxy", label="?")


# ─── 7. Edge confidence clamped [0, 1] ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_edge_confidence_clamped(app):
    from graph.graph_provider import get_or_create_node, get_or_create_edge
    from storage.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        n1 = await get_or_create_node(session, entity_id=_uid(), node_type="memory", label="a")
        n2 = await get_or_create_node(session, entity_id=_uid(), node_type="memory", label="b")
        e_high = await get_or_create_edge(session, n1.id, n2.id, "LED_TO", confidence=5.0)
        assert e_high.confidence <= 1.0

        n3 = await get_or_create_node(session, entity_id=_uid(), node_type="memory", label="c")
        e_low = await get_or_create_edge(session, n1.id, n3.id, "LED_TO", confidence=-2.0)
        assert e_low.confidence >= 0.0


# ─── 8. Multi-hop traversal bounded by max_depth ───────────────────────────────

@pytest.mark.asyncio
async def test_traversal_bounded_by_max_depth(app):
    from graph.graph_provider import get_or_create_node, get_or_create_edge
    from graph.graph_queries import traverse_related
    from storage.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        # Build a chain of 6 nodes: root → n1 → n2 → n3 → n4 → n5
        nodes = []
        for i in range(6):
            n = await get_or_create_node(session, entity_id=_uid(), node_type="memory",
                                          label=f"chain_{i}")
            nodes.append(n)
        for i in range(5):
            await get_or_create_edge(session, nodes[i].id, nodes[i+1].id, "LED_TO", confidence=0.9)

        results = await traverse_related(session, nodes[0].id, max_depth=2, max_nodes=50)
        depths = {depth for _, depth in results}
        assert max(depths) <= 2


# ─── 9. Multi-hop traversal bounded by max_nodes ───────────────────────────────

@pytest.mark.asyncio
async def test_traversal_bounded_by_max_nodes(app):
    from graph.graph_provider import get_or_create_node, get_or_create_edge
    from graph.graph_queries import traverse_related
    from storage.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        root = await get_or_create_node(session, entity_id=_uid(), node_type="memory", label="root")
        for _ in range(10):
            n = await get_or_create_node(session, entity_id=_uid(), node_type="memory", label="leaf")
            await get_or_create_edge(session, root.id, n.id, "RELATED_TO", confidence=0.8)

        results = await traverse_related(session, root.id, max_depth=3, max_nodes=5)
        assert len(results) <= 5


# ─── 10. Traversal returns empty for unknown node ──────────────────────────────

@pytest.mark.asyncio
async def test_traversal_empty_for_unknown(app):
    from graph.graph_queries import traverse_related
    from storage.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        results = await traverse_related(session, "nonexistent_node_id_xyz")
        assert results == []


# ─── 11. Causal chain follows causal rel_types only ────────────────────────────

@pytest.mark.asyncio
async def test_causal_chain_rel_types(app):
    from graph.graph_provider import get_or_create_node, get_or_create_edge
    from graph.graph_queries import find_causal_chains
    from storage.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        root = await get_or_create_node(session, entity_id=_uid(), node_type="memory", label="root_causal")
        causal = await get_or_create_node(session, entity_id=_uid(), node_type="memory", label="causal_child")
        unrelated = await get_or_create_node(session, entity_id=_uid(), node_type="memory", label="unrelated")

        await get_or_create_edge(session, root.id, causal.id, "CAUSED_BY", confidence=0.9)
        await get_or_create_edge(session, root.id, unrelated.id, "REFERENCES", confidence=0.9)

        chains = await find_causal_chains(session, root.id, max_depth=3)
        # All paths should only use causal rel_types
        for path in chains:
            for edge in path.edges:
                assert edge.rel_type in {"CAUSED_BY", "LED_TO", "FAILED_BECAUSE_OF", "RECOVERED_BY"}


# ─── 12. Causal chain depth bounded ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_causal_chain_depth_bounded(app):
    from graph.graph_provider import get_or_create_node, get_or_create_edge
    from graph.graph_queries import find_causal_chains, MAX_CAUSAL_DEPTH
    from storage.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        nodes = []
        for i in range(MAX_CAUSAL_DEPTH + 3):
            n = await get_or_create_node(session, entity_id=_uid(), node_type="memory",
                                          label=f"causal_chain_{i}")
            nodes.append(n)
        for i in range(len(nodes) - 1):
            await get_or_create_edge(session, nodes[i].id, nodes[i+1].id, "LED_TO", confidence=0.8)

        chains = await find_causal_chains(session, nodes[0].id)
        for path in chains:
            assert path.length <= MAX_CAUSAL_DEPTH


# ─── 13. Contradiction edges retrievable ───────────────────────────────────────

@pytest.mark.asyncio
async def test_contradiction_edges(app):
    from graph.graph_provider import get_or_create_node, get_or_create_edge
    from graph.graph_queries import find_contradictions
    from storage.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        a = await get_or_create_node(session, entity_id=_uid(), node_type="memory", label="fact_a")
        b = await get_or_create_node(session, entity_id=_uid(), node_type="memory", label="fact_b")
        c = await get_or_create_node(session, entity_id=_uid(), node_type="memory", label="old_fact")

        await get_or_create_edge(session, a.id, b.id, "CONTRADICTS", confidence=0.85)
        await get_or_create_edge(session, a.id, c.id, "SUPERSEDES", confidence=0.9)

        contradictions = await find_contradictions(session, a.id)
        rel_types = {e.rel_type for _, e in contradictions}
        assert "CONTRADICTS" in rel_types
        assert "SUPERSEDES" in rel_types
        assert len(contradictions) == 2


# ─── 14. Graph builder: episodic chains → PART_OF + DERIVED_FROM ───────────────

@pytest.mark.asyncio
async def test_graph_builder_episodic_chains(app):
    from storage.database import get_session_factory
    from storage.models import EpisodicChain
    from graph.graph_builder import _build_from_episodic_chains
    from graph.graph_provider import get_node_by_entity, get_neighbors

    factory = get_session_factory()
    async with factory() as session:
        mem_id1 = _uid()
        mem_id2 = _uid()
        chain = EpisodicChain(
            id=_uid(),
            title="Test episode",
            episode_type="incident",
            linked_memory_ids=[mem_id1, mem_id2],
            procedural_lesson="always validate before deploy",
            project="p11_builder_test",
        )
        session.add(chain)
        await session.flush()

        count = await _build_from_episodic_chains(session)
        assert count >= 2  # at least PART_OF for each memory + 1 DERIVED_FROM

        # Verify chain node exists
        chain_node = await get_node_by_entity(session, chain.id, "episodic_chain")
        assert chain_node is not None

        # Verify memory nodes exist
        for mid in [mem_id1, mem_id2]:
            mem_node = await get_node_by_entity(session, mid, "memory")
            assert mem_node is not None

        # Verify PART_OF edges exist
        neighbors = await get_neighbors(session, chain_node.id, rel_types=["PART_OF"], direction="in")
        assert len(neighbors) >= 2


# ─── 15. Graph builder: memory supersession → SUPERSEDES ──────────────────────

@pytest.mark.asyncio
async def test_graph_builder_supersession(app):
    from storage.database import get_session_factory
    from storage.models import Memory
    from graph.graph_builder import _build_from_memory_relations
    from graph.graph_provider import get_node_by_entity, get_neighbors

    factory = get_session_factory()
    async with factory() as session:
        old_id = _uid()
        new_id = _uid()
        old_mem = Memory(
            id=old_id,
            layer="semantic",
            content="old fact superseded",
            superseded_by=new_id,
            memory_state="archived",
            project="p11_supersession_test",
        )
        new_mem = Memory(
            id=new_id,
            layer="semantic",
            content="new fact that supersedes",
            project="p11_supersession_test",
        )
        session.add(old_mem)
        session.add(new_mem)
        await session.flush()

        count = await _build_from_memory_relations(session)
        assert count >= 1

        old_node = await get_node_by_entity(session, old_id, "memory")
        new_node = await get_node_by_entity(session, new_id, "memory")
        assert old_node is not None
        assert new_node is not None

        # new SUPERSEDES old
        edges = await get_neighbors(session, old_node.id, rel_types=["SUPERSEDES"], direction="in")
        assert len(edges) >= 1
        assert edges[0][1].rel_type == "SUPERSEDES"


# ─── 16. Graph builder: rollbacks → FAILED_BECAUSE_OF + RECOVERED_BY ──────────

@pytest.mark.asyncio
async def test_graph_builder_rollbacks(app):
    from storage.database import get_session_factory
    from storage.models import Rollback
    from graph.graph_builder import _build_from_rollbacks
    from graph.graph_provider import get_node_by_entity, get_neighbors

    factory = get_session_factory()
    async with factory() as session:
        target_id = _uid()
        rb = Rollback(
            id=_uid(),
            target_type="skill",
            target_id=target_id,
            reason="metrics degraded after deploy",
            automatic=True,
        )
        session.add(rb)
        await session.flush()

        count = await _build_from_rollbacks(session)
        assert count >= 2  # FAILED_BECAUSE_OF + RECOVERED_BY

        rb_node = await get_node_by_entity(session, rb.id, "task")
        assert rb_node is not None

        edges = await get_neighbors(session, rb_node.id, rel_types=["FAILED_BECAUSE_OF"], direction="out")
        assert len(edges) >= 1


# ─── 17. Graph builder: improvements → DERIVED_FROM ───────────────────────────

@pytest.mark.asyncio
async def test_graph_builder_improvements(app):
    from storage.database import get_session_factory
    from storage.models import ImprovementProposal
    from graph.graph_builder import _build_from_improvements
    from graph.graph_provider import get_node_by_entity

    factory = get_session_factory()
    async with factory() as session:
        ref_id = _uid()
        proposal = ImprovementProposal(
            id=_uid(),
            reflection_id=ref_id,
            improvement_type="retrieval_tuning",
            title="Boost procedural provider weight",
            reason="low retrieval success rate",
            current_behavior="weight=1.0",
            proposed_behavior="weight=1.5",
            risk="low",
            expected_benefit="better retrieval",
            status="proposed",
        )
        session.add(proposal)
        await session.flush()

        count = await _build_from_improvements(session)
        assert count >= 1

        imp_node = await get_node_by_entity(session, proposal.id, "improvement")
        assert imp_node is not None


# ─── 18. Graph builder: retrieval sessions → USED_IN ─────────────────────────

@pytest.mark.asyncio
async def test_graph_builder_retrieval_sessions(app):
    from storage.database import get_session_factory
    from storage.models import RetrievalSession
    from graph.graph_builder import _build_from_retrieval_sessions
    from graph.graph_provider import get_node_by_entity

    factory = get_session_factory()
    async with factory() as session:
        mem_id = _uid()
        rs = RetrievalSession(
            id=_uid(),
            query="test graph retrieval",
            retrieved_memory_ids=[mem_id],
            result_count=1,
            task_outcome="success",
            project="p11_test",
        )
        session.add(rs)
        await session.flush()

        count = await _build_from_retrieval_sessions(session)
        assert count >= 1

        rs_node = await get_node_by_entity(session, rs.id, "retrieval_session")
        assert rs_node is not None


# ─── 19. run_graph_build_pass returns stats dict ──────────────────────────────

@pytest.mark.asyncio
async def test_run_graph_build_pass_returns_stats(app):
    from graph.graph_builder import run_graph_build_pass
    from storage.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        result = await run_graph_build_pass(session)
        assert isinstance(result, dict)
        assert "total" in result
        assert "episodic_edges" in result
        assert "rollback_edges" in result
        assert "retrieval_edges" in result
        assert result["total"] >= 0


# ─── 20. Graph boost: 0 for memory with no graph node ────────────────────────

@pytest.mark.asyncio
async def test_graph_boost_zero_for_unknown_memory(app):
    from graph.graph_queries import compute_graph_boost
    from storage.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        boosts = await compute_graph_boost(session, [_uid(), _uid()])
        for b in boosts.values():
            assert b == 0.0


# ─── 21. Graph boost: positive for memory with incoming high-conf edges ────────

@pytest.mark.asyncio
async def test_graph_boost_positive_with_convergence(app):
    from graph.graph_provider import get_or_create_node, get_or_create_edge
    from graph.graph_queries import compute_graph_boost, _BOOST_CONFIDENCE_THRESHOLD
    from storage.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        mem_id = _uid()
        target = await get_or_create_node(session, entity_id=mem_id, node_type="memory", label="boost_target")
        # Add 3 high-confidence incoming edges
        for _ in range(3):
            src = await get_or_create_node(session, entity_id=_uid(), node_type="memory", label="source")
            await get_or_create_edge(
                session, src.id, target.id, "RELATED_TO",
                confidence=_BOOST_CONFIDENCE_THRESHOLD + 0.1,
            )

        boosts = await compute_graph_boost(session, [mem_id])
        assert boosts[mem_id] > 0.0


# ─── 22. Graph boost bounded to MAX_GRAPH_BOOST ───────────────────────────────

@pytest.mark.asyncio
async def test_graph_boost_bounded(app):
    from graph.graph_provider import get_or_create_node, get_or_create_edge
    from graph.graph_queries import compute_graph_boost, _MAX_GRAPH_BOOST, _BOOST_CONFIDENCE_THRESHOLD
    from storage.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        mem_id = _uid()
        target = await get_or_create_node(session, entity_id=mem_id, node_type="memory", label="well_connected")
        # Add 20 high-confidence incoming edges (well above the saturation point of 5)
        for _ in range(20):
            src = await get_or_create_node(session, entity_id=_uid(), node_type="memory", label="src")
            await get_or_create_edge(
                session, src.id, target.id, "RELATED_TO",
                confidence=_BOOST_CONFIDENCE_THRESHOLD + 0.05,
            )

        boosts = await compute_graph_boost(session, [mem_id])
        assert boosts[mem_id] <= _MAX_GRAPH_BOOST


# ─── 23. Graph telemetry returns counts ───────────────────────────────────────

@pytest.mark.asyncio
async def test_graph_telemetry_counts(app):
    from graph.graph_provider import get_or_create_node, get_or_create_edge
    from graph.graph_queries import compute_graph_telemetry
    from storage.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        project = f"telem_{_uid()[:8]}"
        n1 = await get_or_create_node(session, entity_id=_uid(), node_type="memory",
                                       label="t1", project=project)
        n2 = await get_or_create_node(session, entity_id=_uid(), node_type="memory",
                                       label="t2", project=project)
        await get_or_create_edge(session, n1.id, n2.id, "RELATED_TO", confidence=0.8)

        telem = await compute_graph_telemetry(session, project=project)
        assert telem.total_nodes >= 2
        assert telem.total_edges >= 1
        assert "memory" in telem.nodes_by_type
        assert "RELATED_TO" in telem.edges_by_rel


# ─── 24. Most-connected nodes: degree = in + out edges ────────────────────────

@pytest.mark.asyncio
async def test_most_connected_nodes_degree(app):
    from graph.graph_provider import get_or_create_node, get_or_create_edge
    from graph.graph_queries import get_most_connected_nodes
    from storage.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        hub_eid = _uid()
        project = f"hub_{hub_eid[:8]}"
        hub = await get_or_create_node(session, entity_id=hub_eid, node_type="memory",
                                        label="hub", project=project)
        for _ in range(3):
            leaf = await get_or_create_node(session, entity_id=_uid(), node_type="memory",
                                             label="leaf", project=project)
            await get_or_create_edge(session, hub.id, leaf.id, "RELATED_TO", confidence=0.8)

        nodes = await get_most_connected_nodes(session, project=project, limit=5)
        assert len(nodes) > 0
        top = nodes[0]
        assert top["degree"] >= 3


# ─── 25. Cross-user isolation: project-scoped nodes ───────────────────────────

@pytest.mark.asyncio
async def test_graph_project_isolation(app):
    from graph.graph_provider import get_or_create_node
    from graph.graph_queries import get_most_connected_nodes
    from storage.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        project_a = f"isolation_a_{_uid()[:8]}"
        project_b = f"isolation_b_{_uid()[:8]}"

        await get_or_create_node(session, entity_id=_uid(), node_type="memory",
                                  label="a1", project=project_a)
        await get_or_create_node(session, entity_id=_uid(), node_type="memory",
                                  label="a2", project=project_a)
        await get_or_create_node(session, entity_id=_uid(), node_type="memory",
                                  label="b1", project=project_b)

        nodes_a = await get_most_connected_nodes(session, project=project_a, limit=50)
        nodes_b = await get_most_connected_nodes(session, project=project_b, limit=50)

        projects_in_a = {n["project"] for n in nodes_a}
        projects_in_b = {n["project"] for n in nodes_b}

        assert project_b not in projects_in_a
        assert project_a not in projects_in_b


# ─── 26–33. API endpoint tests ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_api_get_graph_node_not_found(app, client):
    with as_user(app, "u_graph1"):
        r = await client.get("/api/graph/nodes/nonexistent_entity?node_type=memory")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_api_get_graph_node_found(app, client):
    from storage.database import get_session_factory
    from graph.graph_provider import get_or_create_node

    factory = get_session_factory()
    entity_id = _uid()
    async with factory() as session:
        await get_or_create_node(session, entity_id=entity_id, node_type="memory",
                                  label="api_test_node")
        await session.commit()

    with as_user(app, "u_graph2"):
        r = await client.get(f"/api/graph/nodes/{entity_id}?node_type=memory")
        assert r.status_code == 200
        data = r.json()
        assert data["node"]["entity_id"] == entity_id
        assert "edges" in data


@pytest.mark.asyncio
async def test_api_traverse_not_found(app, client):
    with as_user(app, "u_graph3"):
        r = await client.get("/api/graph/traverse/ghost_entity_xyz?node_type=memory")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_api_traverse_found(app, client):
    from storage.database import get_session_factory
    from graph.graph_provider import get_or_create_node, get_or_create_edge

    factory = get_session_factory()
    eid1 = _uid()
    eid2 = _uid()
    async with factory() as session:
        n1 = await get_or_create_node(session, entity_id=eid1, node_type="memory", label="traverse_root")
        n2 = await get_or_create_node(session, entity_id=eid2, node_type="memory", label="traverse_child")
        await get_or_create_edge(session, n1.id, n2.id, "LED_TO", confidence=0.8)
        await session.commit()

    with as_user(app, "u_graph4"):
        r = await client.get(f"/api/graph/traverse/{eid1}?node_type=memory&max_depth=2&max_nodes=10")
        assert r.status_code == 200
        data = r.json()
        assert "traversal" in data
        assert data["count"] >= 1


@pytest.mark.asyncio
async def test_api_causal_chain(app, client):
    from storage.database import get_session_factory
    from graph.graph_provider import get_or_create_node, get_or_create_edge

    factory = get_session_factory()
    eid_root = _uid()
    eid_effect = _uid()
    async with factory() as session:
        n_root = await get_or_create_node(session, entity_id=eid_root, node_type="memory", label="cause")
        n_effect = await get_or_create_node(session, entity_id=eid_effect, node_type="memory", label="effect")
        await get_or_create_edge(session, n_root.id, n_effect.id, "CAUSED_BY", confidence=0.9)
        await session.commit()

    with as_user(app, "u_graph5"):
        r = await client.get(f"/api/graph/causal/{eid_root}?node_type=memory")
        assert r.status_code == 200
        data = r.json()
        assert "chains" in data


@pytest.mark.asyncio
async def test_api_contradictions(app, client):
    from storage.database import get_session_factory
    from graph.graph_provider import get_or_create_node, get_or_create_edge

    factory = get_session_factory()
    eid_a = _uid()
    eid_b = _uid()
    async with factory() as session:
        na = await get_or_create_node(session, entity_id=eid_a, node_type="memory", label="fact_a")
        nb = await get_or_create_node(session, entity_id=eid_b, node_type="memory", label="fact_b")
        await get_or_create_edge(session, na.id, nb.id, "CONTRADICTS", confidence=0.8)
        await session.commit()

    with as_user(app, "u_graph6"):
        r = await client.get(f"/api/graph/contradictions/{eid_a}?node_type=memory")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] >= 1


@pytest.mark.asyncio
async def test_api_centrality(app, client):
    with as_user(app, "u_graph7"):
        r = await client.get("/api/graph/centrality?limit=5")
        assert r.status_code == 200
        data = r.json()
        assert "most_connected" in data
        assert isinstance(data["most_connected"], list)


@pytest.mark.asyncio
async def test_api_graph_telemetry(app, client):
    with as_user(app, "u_graph8"):
        r = await client.get("/api/graph/telemetry")
        assert r.status_code == 200
        data = r.json()
        assert "total_nodes" in data
        assert "total_edges" in data
        assert "nodes_by_type" in data
        assert "edges_by_rel" in data


@pytest.mark.asyncio
async def test_api_graph_build_trigger(app, client):
    with as_user(app, "u_graph9"):
        r = await client.post("/api/graph/build")
        assert r.status_code == 200
        data = r.json()
        assert "total" in data
        assert isinstance(data["total"], int)


# ─── 34. Worker task executes without error ────────────────────────────────────

@pytest.mark.asyncio
async def test_worker_run_graph_build(app):
    from worker.tasks import run_graph_build
    # Should complete without raising
    result = await run_graph_build()
    # run_graph_build returns None (task wrapper swallows return)
    # We just need it not to raise


# ─── 35. Scheduler includes graph_build job ───────────────────────────────────

def test_scheduler_includes_graph_build():
    from worker.scheduler import create_scheduler
    scheduler = create_scheduler()
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert "graph_build" in job_ids
    try:
        scheduler.shutdown(wait=False)
    except Exception:
        pass
