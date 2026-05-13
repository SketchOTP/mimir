"""ChromaDB wrapper for semantic vector search."""

from __future__ import annotations

import logging
from datetime import datetime, UTC
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings
from sentence_transformers import SentenceTransformer

from mimir.config import get_settings

logger = logging.getLogger(__name__)

_client: chromadb.ClientAPI | None = None
_model: SentenceTransformer | None = None

_COLLECTIONS = {
    "episodic": "mimir_episodic",
    "semantic": "mimir_semantic",
    "procedural": "mimir_procedural",
    "working": "mimir_working",
}

# Required metadata keys that every vector must carry for isolation and trust filtering
_REQUIRED_META_KEYS = (
    "user_id", "project_id", "memory_id", "layer", "importance", "created_at",
    "trust_score", "verification_status", "memory_state",
)


def _get_client() -> chromadb.ClientAPI:
    global _client
    if _client is None:
        settings = get_settings()
        _client = chromadb.PersistentClient(
            path=str(settings.vector_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
    return _client


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        settings = get_settings()
        logger.info("Loading embedding model %s", settings.embedding_model)
        _model = SentenceTransformer(settings.embedding_model)
    return _model


def _collection(layer: str) -> chromadb.Collection:
    name = _COLLECTIONS.get(layer, f"mimir_{layer}")
    return _get_client().get_or_create_collection(name, metadata={"hnsw:space": "cosine"})


def embed(texts: list[str]) -> list[list[float]]:
    return _get_model().encode(texts, normalize_embeddings=True).tolist()


def _build_metadata(
    layer: str,
    memory_id: str,
    *,
    user_id: str | None = None,
    project_id: str | None = None,
    importance: float = 0.5,
    created_at: str | None = None,
    trust_score: float = 0.7,
    verification_status: str = "trusted_system_observed",
    memory_state: str = "active",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a fully-populated metadata dict for isolation and trust filtering."""
    meta: dict[str, Any] = {
        "memory_id": memory_id,
        "layer": layer,
        "user_id": user_id or "",
        "project_id": project_id or "",
        "importance": importance,
        "created_at": created_at or datetime.now(UTC).isoformat(),
        "trust_score": trust_score,
        "verification_status": verification_status,
        "memory_state": memory_state,
    }
    if extra:
        for k, v in extra.items():
            if k not in meta:
                meta[k] = v
    return meta


def upsert(
    layer: str,
    memory_id: str,
    content: str,
    *,
    user_id: str | None = None,
    project_id: str | None = None,
    importance: float = 0.5,
    created_at: str | None = None,
    trust_score: float = 0.7,
    verification_status: str = "trusted_system_observed",
    memory_state: str = "active",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Upsert a vector with full isolation and trust metadata."""
    col = _collection(layer)
    vectors = embed([content])
    meta = _build_metadata(
        layer, memory_id,
        user_id=user_id,
        project_id=project_id,
        importance=importance,
        created_at=created_at,
        trust_score=trust_score,
        verification_status=verification_status,
        memory_state=memory_state,
        extra=metadata,
    )
    col.upsert(ids=[memory_id], embeddings=vectors, documents=[content], metadatas=[meta])


def search(
    layer: str | None,
    query: str,
    n_results: int = 10,
    where: dict | None = None,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return list of {id, content, distance, metadata}.

    When user_id is provided, results are filtered to that user only.
    This is the primary vector isolation boundary.
    """
    layers = list(_COLLECTIONS.keys()) if layer is None else [layer]
    vector = embed([query])[0]
    hits: list[dict] = []

    # Build isolation filter — user_id always takes precedence
    effective_where: dict | None = where
    if user_id:
        user_filter = {"user_id": {"$eq": user_id}}
        if effective_where:
            effective_where = {"$and": [effective_where, user_filter]}
        else:
            effective_where = user_filter

    for lyr in layers:
        col = _collection(lyr)
        try:
            count = col.count()
            if count == 0:
                continue
            res = col.query(
                query_embeddings=[vector],
                n_results=min(n_results, count),
                where=effective_where,
                include=["documents", "distances", "metadatas"],
            )
            for i, doc_id in enumerate(res["ids"][0]):
                hits.append(
                    {
                        "id": doc_id,
                        "content": res["documents"][0][i],
                        "distance": res["distances"][0][i],
                        "score": 1.0 - res["distances"][0][i],
                        "layer": lyr,
                        "metadata": res["metadatas"][0][i],
                    }
                )
        except Exception as e:
            logger.warning("Vector search failed for layer %s: %s", lyr, e)

    hits.sort(key=lambda x: x["score"], reverse=True)
    return hits[:n_results]


def delete(layer: str, memory_id: str) -> None:
    col = _collection(layer)
    col.delete(ids=[memory_id])


def count(layer: str | None = None) -> int:
    if layer:
        return _collection(layer).count()
    return sum(_collection(lyr).count() for lyr in _COLLECTIONS)
