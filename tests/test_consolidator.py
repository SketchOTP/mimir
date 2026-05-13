"""Memory consolidator dedup CI tests.

Verifies that deduplicate_semantic():
  - Soft-deletes the lower-importance duplicate when two memories are near-identical
  - Keeps the higher-importance memory intact with its metadata preserved
  - Does NOT merge memories with unrelated content
"""

import pytest
import uuid
from datetime import datetime, UTC

from storage.models import Memory
from memory.memory_consolidator import deduplicate_semantic
from storage import vector_store


def _unique_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


@pytest.mark.asyncio
async def test_duplicate_memories_merge(app):
    """deduplicate_semantic() soft-deletes the lower-importance duplicate."""
    from storage.database import get_session_factory

    project = f"dedup_merge_{uuid.uuid4().hex[:8]}"
    content = f"User's preferred name is Tym, never call them Timothy [{uuid.uuid4().hex[:6]}]"
    id_high = _unique_id("dedup_high")
    id_low = _unique_id("dedup_low")

    # Insert both directly — bypass semantic_store's store-time dedup guard
    async with get_session_factory()() as session:
        session.add_all([
            Memory(
                id=id_high, layer="semantic", content=content, importance=0.8,
                project=project,
                created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
            ),
            Memory(
                id=id_low, layer="semantic", content=content, importance=0.4,
                project=project,
                created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
            ),
        ])
        await session.commit()

    # Add both to the vector store — identical text → cosine similarity ≈ 1.0
    vector_store.upsert("semantic", id_high, content, importance=0.8)
    vector_store.upsert("semantic", id_low, content, importance=0.4)

    async with get_session_factory()() as session:
        removed = await deduplicate_semantic(session, threshold=0.97, project=project)

    assert removed >= 1, "Expected at least 1 duplicate to be removed"

    async with get_session_factory()() as session:
        high = await session.get(Memory, id_high)
        low = await session.get(Memory, id_low)
        assert low is not None and low.deleted_at is not None, (
            "Lower-importance duplicate should be soft-deleted"
        )
        assert high is not None and high.deleted_at is None, (
            "Higher-importance memory should be kept"
        )


@pytest.mark.asyncio
async def test_important_metadata_preserved_after_dedup(app):
    """The surviving memory retains its importance score and full content."""
    from storage.database import get_session_factory

    project = f"dedup_meta_{uuid.uuid4().hex[:8]}"
    content = f"Project always uses async SQLAlchemy for all DB operations [{uuid.uuid4().hex[:6]}]"
    id_a = _unique_id("meta_high")
    id_b = _unique_id("meta_low")

    async with get_session_factory()() as session:
        session.add_all([
            Memory(
                id=id_a, layer="semantic", content=content, importance=0.9,
                project=project,
                created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
            ),
            Memory(
                id=id_b, layer="semantic", content=content, importance=0.2,
                project=project,
                created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
            ),
        ])
        await session.commit()

    vector_store.upsert("semantic", id_a, content, importance=0.9)
    vector_store.upsert("semantic", id_b, content, importance=0.2)

    async with get_session_factory()() as session:
        await deduplicate_semantic(session, threshold=0.97, project=project)

    async with get_session_factory()() as session:
        survivor = await session.get(Memory, id_a)
        assert survivor is not None
        assert survivor.deleted_at is None, "High-importance memory must not be deleted"
        assert survivor.importance == 0.9, "Importance score must be preserved"
        assert survivor.content == content, "Content must be preserved"


@pytest.mark.asyncio
async def test_unrelated_memories_not_merged(app):
    """Semantically unrelated memories are never soft-deleted by dedup."""
    from storage.database import get_session_factory

    project = f"dedup_unrelated_{uuid.uuid4().hex[:8]}"
    suffix = uuid.uuid4().hex[:6]
    id_x = _unique_id("nodedup_x")
    id_y = _unique_id("nodedup_y")
    # Completely unrelated content — cosine similarity will be well below 0.97
    content_x = f"User prefers Python over JavaScript for backend services [{suffix}A]"
    content_y = f"The kitchen thermostat is set to 21 degrees Celsius [{suffix}B]"

    async with get_session_factory()() as session:
        session.add_all([
            Memory(
                id=id_x, layer="semantic", content=content_x, importance=0.7,
                project=project,
                created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
            ),
            Memory(
                id=id_y, layer="semantic", content=content_y, importance=0.7,
                project=project,
                created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
            ),
        ])
        await session.commit()

    vector_store.upsert("semantic", id_x, content_x, importance=0.7)
    vector_store.upsert("semantic", id_y, content_y, importance=0.7)

    async with get_session_factory()() as session:
        await deduplicate_semantic(session, threshold=0.97, project=project)

    async with get_session_factory()() as session:
        x = await session.get(Memory, id_x)
        y = await session.get(Memory, id_y)
        assert x is not None and x.deleted_at is None, "Unrelated memory x must not be deleted"
        assert y is not None and y.deleted_at is None, "Unrelated memory y must not be deleted"
