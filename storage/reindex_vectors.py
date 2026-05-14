"""Rebuild all vector embeddings with full isolation metadata.

Usage:
    python -m mimir.storage.reindex_vectors
    python -m storage.reindex_vectors
"""

from __future__ import annotations

import asyncio
import logging
import sys

from sqlalchemy import select

from storage.database import get_session_factory, init_db
from storage.models import Memory
from storage import vector_store

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def reindex() -> int:
    await init_db()
    factory = get_session_factory()
    total = 0
    errors = 0

    async with factory() as session:
        q = select(Memory).where(Memory.deleted_at.is_(None))
        result = await session.execute(q)
        memories = list(result.scalars())

    logger.info("Reindexing %d memories...", len(memories))

    for mem in memories:
        try:
            vector_store.upsert(
                mem.layer,
                mem.id,
                mem.content,
                user_id=mem.user_id,
                project_id=mem.project,
                importance=mem.importance,
                created_at=mem.created_at.isoformat() if mem.created_at else None,
                trust_score=mem.trust_score if mem.trust_score is not None else 0.7,
                verification_status=mem.verification_status or "trusted_system_observed",
                memory_state=mem.memory_state or "active",
                source_type=mem.source_type,
                metadata=mem.meta,
            )
            total += 1
            if total % 100 == 0:
                logger.info("  %d / %d reindexed", total, len(memories))
        except Exception as exc:
            logger.error("Failed to reindex %s: %s", mem.id, exc)
            errors += 1

    logger.info("Reindex complete: %d ok, %d errors", total, errors)
    return total


if __name__ == "__main__":
    count = asyncio.run(reindex())
    sys.exit(0 if count >= 0 else 1)
