"""SQLite FTS5 full-text search for the keyword retrieval provider.

The FTS5 virtual table `memory_fts` was created by migration 0008 and
extended by migration 0011 to include user_id and project_id isolation
columns.  This module provides user/project-scoped search and a graceful
fallback to LIKE-based matching when FTS5 is unavailable or the old
(pre-0011) schema is in place.

Schema (post-0011):
    memory_fts(memory_id UNINDEXED, user_id UNINDEXED, project_id UNINDEXED, content)

Isolation contract:
  - NULL user_id in memories is stored as '' in the FTS table.
  - Filtering: (user_id = :uid OR user_id = '') to include shared memories.
  - Filtering: (project_id = :proj OR project_id = '') for project scope.
  - No filter applied when the corresponding parameter is None (open access).
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Lazily probed: None = not yet checked, True = new schema available,
# False = FTS5 unavailable or old schema (fall back to LIKE)
_FTS5_AVAILABLE: bool | None = None


async def _probe_fts5(session: AsyncSession) -> bool:
    """Check once whether memory_fts has the post-0011 schema."""
    global _FTS5_AVAILABLE
    if _FTS5_AVAILABLE is not None:
        return _FTS5_AVAILABLE
    try:
        # Verify both the FTS5 engine and the isolation columns exist
        await session.execute(
            text(
                "SELECT memory_id, user_id, project_id "
                "FROM memory_fts WHERE content MATCH 'probe' LIMIT 1"
            )
        )
        _FTS5_AVAILABLE = True
    except Exception:
        _FTS5_AVAILABLE = False
        logger.debug(
            "FTS5 memory_fts (post-0011 schema) not available — "
            "keyword provider uses LIKE fallback"
        )
    return _FTS5_AVAILABLE


def _build_fts_query(query: str) -> str | None:
    """Build an FTS5 OR query from free-text. Returns None if no usable terms."""
    clean = re.sub(r"[^\w\s]", " ", query.lower())
    words = [w for w in clean.split() if len(w) >= 3]
    if not words:
        return None
    return " OR ".join(f'"{w}"' for w in words[:10])


async def fts5_search(
    session: AsyncSession,
    query: str,
    *,
    limit: int = 20,
    user_id: str | None = None,
    project_id: str | None = None,
) -> list[tuple[str, float]]:
    """Full-text search using SQLite FTS5 with user/project isolation.

    Applies user_id and project_id filters at the FTS level when provided.
    NULL user_id / project memories are stored as '' and always included
    (shared memories are visible to all users within a project).

    Returns list of (memory_id, bm25_score) sorted descending. Empty on failure.
    """
    if not await _probe_fts5(session):
        return []

    fts_query = _build_fts_query(query)
    if not fts_query:
        return []

    # Build isolation clauses for UNINDEXED columns
    params: dict = {"q": fts_query, "lim": limit}
    isolation_clauses: list[str] = []

    if user_id is not None:
        isolation_clauses.append("(user_id = :uid OR user_id = '')")
        params["uid"] = user_id

    if project_id is not None:
        isolation_clauses.append("(project_id = :proj OR project_id = '')")
        params["proj"] = project_id

    where_extra = (" AND " + " AND ".join(isolation_clauses)) if isolation_clauses else ""

    sql = text(f"""
        SELECT memory_id, bm25(memory_fts) * -1 AS score
        FROM memory_fts
        WHERE content MATCH :q{where_extra}
        ORDER BY score DESC
        LIMIT :lim
    """)

    try:
        result = await session.execute(sql, params)
        rows = result.fetchall()
        return [(str(row[0]), float(row[1])) for row in rows if row[1] is not None]
    except Exception as exc:
        logger.warning("FTS5 search failed: %s", exc)
        return []


async def reindex_fts(session: AsyncSession) -> int:
    """Rebuild the FTS5 index from the memories table.

    Clears all FTS rows then reloads from live memories.  Safe to call at
    any time; the FTS table is purely a search index.  Returns row count.
    """
    if not await _probe_fts5(session):
        logger.warning("reindex_fts: FTS5 (post-0011 schema) not available, skipping")
        return 0

    try:
        await session.execute(text("DELETE FROM memory_fts"))
        result = await session.execute(text(
            "INSERT INTO memory_fts(memory_id, user_id, project_id, content) "
            "SELECT id, COALESCE(user_id, ''), COALESCE(project, ''), content "
            "FROM memories "
            "WHERE deleted_at IS NULL AND memory_state != 'quarantined'"
        ))
        await session.commit()
        count = result.rowcount if result.rowcount is not None else 0
        logger.info("reindex_fts: indexed %d memories", count)
        return count
    except Exception as exc:
        await session.rollback()
        logger.error("reindex_fts failed: %s", exc)
        return 0


def reset_fts5_probe() -> None:
    """Reset the FTS5 availability probe (for testing)."""
    global _FTS5_AVAILABLE
    _FTS5_AVAILABLE = None
