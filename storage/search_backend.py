"""Keyword search backend abstraction.

Three backends are available:
  SQLiteFTSBackend   — uses SQLite FTS5 virtual table (fast, BM25-ranked)
  PostgresSearchBackend — uses tsvector / plainto_tsquery (Postgres native FTS)
  LikeFallbackBackend   — plain LIKE match; works on any SQL database

The active backend is selected automatically from the DB dialect, or can be
overridden by setting MIMIR_SEARCH_BACKEND env var to 'fts5', 'postgres', or 'like'.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass
class SearchHit:
    memory_id: str
    score: float          # normalised to [0, 1]


class SearchBackend:
    """Base class — override search(), reindex(), healthcheck()."""

    async def search(
        self,
        session: AsyncSession,
        query: str,
        user_id: str | None = None,
        project_id: str | None = None,
        limit: int = 10,
    ) -> list[SearchHit]:
        raise NotImplementedError

    async def reindex(self, session: AsyncSession) -> int:
        """Rebuild the full-text index. Returns the number of rows indexed."""
        return 0

    async def healthcheck(self, session: AsyncSession) -> bool:
        """Return True if the backend is operational."""
        return True


# ── SQLite FTS5 ───────────────────────────────────────────────────────────────

class SQLiteFTSBackend(SearchBackend):
    """SQLite FTS5 backend with user/project isolation and BM25 ranking."""

    async def search(
        self,
        session: AsyncSession,
        query: str,
        user_id: str | None = None,
        project_id: str | None = None,
        limit: int = 10,
    ) -> list[SearchHit]:
        if not query.strip():
            return []

        # Escape FTS5 special characters
        safe_query = query.replace('"', '""')
        params: dict = {"q": safe_query, "lim": limit}

        uid_clause = ""
        if user_id is not None:
            uid_clause = "AND (user_id = :uid OR user_id = '')"
            params["uid"] = user_id

        proj_clause = ""
        if project_id is not None:
            proj_clause = "AND (project_id = :proj OR project_id = '')"
            params["proj"] = project_id

        sql = f"""
            SELECT memory_id, bm25(memory_fts) AS score
            FROM memory_fts
            WHERE memory_fts MATCH :q
              {uid_clause}
              {proj_clause}
            ORDER BY score
            LIMIT :lim
        """
        try:
            result = await session.execute(text(sql), params)
            rows = result.fetchall()
        except Exception as exc:
            logger.debug("FTS5 search error: %s", exc)
            return []

        if not rows:
            return []

        # bm25() returns negative values — negate and normalise to [0, 1]
        scores = [-r[1] for r in rows]
        max_score = max(scores) or 1.0
        return [
            SearchHit(memory_id=r[0], score=min(1.0, s / max_score))
            for r, s in zip(rows, scores)
        ]

    async def reindex(self, session: AsyncSession) -> int:
        """Rebuild the FTS5 table from the memories table."""
        await session.execute(text("DELETE FROM memory_fts"))
        result = await session.execute(text(
            "INSERT INTO memory_fts(memory_id, user_id, project_id, content) "
            "SELECT id, COALESCE(user_id,''), COALESCE(project,''), content "
            "FROM memories WHERE deleted_at IS NULL AND memory_state != 'quarantined'"
        ))
        await session.commit()
        return result.rowcount or 0

    async def healthcheck(self, session: AsyncSession) -> bool:
        try:
            await session.execute(text(
                "SELECT memory_id, user_id, project_id FROM memory_fts LIMIT 1"
            ))
            return True
        except Exception:
            return False


# ── Postgres tsvector ─────────────────────────────────────────────────────────

class PostgresSearchBackend(SearchBackend):
    """Postgres full-text search using tsvector column and plainto_tsquery."""

    async def search(
        self,
        session: AsyncSession,
        query: str,
        user_id: str | None = None,
        project_id: str | None = None,
        limit: int = 10,
    ) -> list[SearchHit]:
        if not query.strip():
            return []

        normalized = " ".join(query.replace("_", " ").strip().split())
        label = normalized.lower().replace(" ", "_")
        params: dict = {"q": normalized or query, "raw": query, "norm": normalized or query, "label": label, "lim": limit}
        uid_clause = ""
        if user_id is not None:
            uid_clause = "AND (user_id = :uid OR user_id IS NULL)"
            params["uid"] = user_id

        proj_clause = ""
        if project_id is not None:
            proj_clause = "AND (project = :proj OR project IS NULL)"
            params["proj"] = project_id

        # Use tsvector index if available; fall back to plainto_tsquery over content
        sql = f"""
            SELECT id AS memory_id,
                   GREATEST(
                       ts_rank_cd(to_tsvector('english', content), plainto_tsquery('english', :q)),
                       CASE
                           WHEN LOWER(content) LIKE '%' || LOWER(:raw) || '%' THEN 0.99
                           WHEN LOWER(REPLACE(content, '_', ' ')) LIKE '%' || LOWER(:norm) || '%' THEN 0.97
                           WHEN LOWER(COALESCE(meta->>'capsule_type', meta->>'bootstrap_type', '')) = :label THEN 1.0
                           ELSE 0.0
                       END
                   ) AS score
            FROM memories
            WHERE deleted_at IS NULL
              AND memory_state != 'quarantined'
              AND (
                    to_tsvector('english', content) @@ plainto_tsquery('english', :q)
                    OR LOWER(content) LIKE '%' || LOWER(:raw) || '%'
                    OR LOWER(REPLACE(content, '_', ' ')) LIKE '%' || LOWER(:norm) || '%'
                    OR LOWER(COALESCE(meta->>'capsule_type', meta->>'bootstrap_type', '')) = :label
                  )
              {uid_clause}
              {proj_clause}
            ORDER BY score DESC
            LIMIT :lim
        """
        try:
            result = await session.execute(text(sql), params)
            rows = result.fetchall()
        except Exception as exc:
            logger.warning("Postgres FTS search error: %s", exc)
            return []

        if not rows:
            return []

        max_score = max(r[1] for r in rows) or 1.0
        return [
            SearchHit(memory_id=r[0], score=min(1.0, float(r[1]) / max_score))
            for r in rows
        ]

    async def reindex(self, session: AsyncSession) -> int:
        # No persistent index to rebuild — tsvector is computed on the fly.
        # Optionally, create a GIN index: handled by migration.
        return 0

    async def healthcheck(self, session: AsyncSession) -> bool:
        try:
            await session.execute(text(
                "SELECT to_tsvector('english', 'healthcheck') @@ plainto_tsquery('english', 'healthcheck')"
            ))
            return True
        except Exception:
            return False


# ── LIKE fallback ─────────────────────────────────────────────────────────────

class LikeFallbackBackend(SearchBackend):
    """Plain LIKE match — portable but slow on large tables, no ranking."""

    async def search(
        self,
        session: AsyncSession,
        query: str,
        user_id: str | None = None,
        project_id: str | None = None,
        limit: int = 10,
    ) -> list[SearchHit]:
        if not query.strip():
            return []

        # Split into terms; all must match (AND semantics)
        terms = [t.strip() for t in query.split() if t.strip()]
        if not terms:
            return []

        params: dict = {"lim": limit}
        term_clauses = []
        for i, term in enumerate(terms[:5]):  # cap at 5 terms
            params[f"t{i}"] = f"%{term}%"
            term_clauses.append(f"content LIKE :t{i}")

        uid_clause = ""
        if user_id is not None:
            uid_clause = "AND (user_id = :uid OR user_id IS NULL)"
            params["uid"] = user_id

        proj_clause = ""
        if project_id is not None:
            proj_clause = "AND (project = :proj OR project IS NULL)"
            params["proj"] = project_id

        where = " AND ".join(term_clauses)
        sql = f"""
            SELECT id AS memory_id
            FROM memories
            WHERE deleted_at IS NULL
              AND memory_state != 'quarantined'
              AND {where}
              {uid_clause}
              {proj_clause}
            ORDER BY created_at DESC
            LIMIT :lim
        """
        try:
            result = await session.execute(text(sql), params)
            rows = result.fetchall()
        except Exception as exc:
            logger.debug("LIKE search error: %s", exc)
            return []

        return [SearchHit(memory_id=r[0], score=0.5) for r in rows]

    async def healthcheck(self, session: AsyncSession) -> bool:
        return True


# ── Backend selection ─────────────────────────────────────────────────────────

_backend_instance: SearchBackend | None = None


def get_search_backend(dialect: str | None = None) -> SearchBackend:
    """Return the active search backend singleton.

    Selection order:
    1. MIMIR_SEARCH_BACKEND env var ('fts5', 'postgres', 'like')
    2. Auto-detect from DB dialect
    """
    global _backend_instance
    if _backend_instance is not None:
        return _backend_instance

    override = os.environ.get("MIMIR_SEARCH_BACKEND", "").lower()

    if override == "fts5":
        _backend_instance = SQLiteFTSBackend()
    elif override == "postgres":
        _backend_instance = PostgresSearchBackend()
    elif override == "like":
        _backend_instance = LikeFallbackBackend()
    else:
        if dialect is None:
            try:
                from storage.database import get_db_dialect
                dialect = get_db_dialect()
            except Exception:
                dialect = "sqlite"

        if dialect == "postgresql":
            _backend_instance = PostgresSearchBackend()
        else:
            _backend_instance = SQLiteFTSBackend()

    logger.info("Search backend: %s", type(_backend_instance).__name__)
    return _backend_instance


def reset_search_backend() -> None:
    """Reset singleton — used in tests when switching backends."""
    global _backend_instance
    _backend_instance = None
