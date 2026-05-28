"""ChromaDB integration + ranked semantic search.

One collection (`synapse_nodes`) holds every embedding. Node `type` and `tags`
are stored as ChromaDB metadata so callers can filter at query time.

Ranking blends semantic distance with graph centrality: a node with many
incoming/outgoing edges is more "load-bearing" for the user and ranks higher
at equal semantic similarity. See `SEARCH_CENTRALITY_BOOST` in `synapse/config.py`.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any

import numpy as np
from loguru import logger

from synapse.config import (
    CHROMA_COLLECTION_NAME,
    SEARCH_CENTRALITY_BOOST,
    SEARCH_DEFAULT_LIMIT,
    SEARCH_FRESHNESS_WEIGHT,
    get_settings,
)
from synapse.graph.embeddings import embed_text

_client: Any | None = None
_collection: Any | None = None
_client_lock = threading.Lock()


def _client_and_collection() -> tuple[Any, Any]:
    """Lazy-construct the PersistentClient + collection."""
    global _client, _collection
    if _collection is not None:
        return _client, _collection
    with _client_lock:
        if _collection is None:
            import chromadb
            from chromadb.config import Settings as ChromaSettings

            settings = get_settings()
            settings.chroma_dir.mkdir(parents=True, exist_ok=True)
            _client = chromadb.PersistentClient(
                path=str(settings.chroma_dir),
                settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True),
            )
            _collection = _client.get_or_create_collection(
                name=CHROMA_COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(
                "chroma collection ready: {name} at {path}",
                name=CHROMA_COLLECTION_NAME,
                path=settings.chroma_dir,
            )
    return _client, _collection


def reset_client() -> None:
    """Drop cached client + collection refs. Test-only."""
    global _client, _collection
    _client = None
    _collection = None


# ── Write path ───────────────────────────────────────────────────────────────


def _serialize_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Coerce metadata into Chroma-acceptable scalars (str/int/float/bool).

    Lists/dicts are JSON-encoded so they round-trip through the metadata API.
    """
    out: dict[str, Any] = {}
    for k, v in meta.items():
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)):
            out[k] = v
        else:
            out[k] = json.dumps(v, default=str)
    return out


def upsert_node_embedding(
    *,
    node_id: str,
    text: str,
    metadata: dict[str, Any],
    embedding: np.ndarray | None = None,
) -> None:
    """Add or replace the embedding for a node.

    Args:
        node_id: SQLModel node primary key.
        text: Source text the embedding was derived from (stored as document).
        metadata: Node metadata for filtering (`type`, `tags`, etc.). Must contain
            at minimum `type`.
        embedding: Optional precomputed vector. If None, embed `text` now.
    """
    _, coll = _client_and_collection()
    vec = embedding if embedding is not None else embed_text(text)
    coll.upsert(
        ids=[node_id],
        embeddings=[vec.tolist()],
        documents=[text],
        metadatas=[_serialize_metadata(metadata)],
    )


def delete_node_embedding(node_id: str) -> None:
    """Remove a node's embedding. Use with care — graph nodes are never deleted,
    but on update the old embedding is replaced (upsert handles that)."""
    _, coll = _client_and_collection()
    coll.delete(ids=[node_id])


# ── Read path ────────────────────────────────────────────────────────────────


@dataclass
class SearchHit:
    """One result row from a semantic search."""

    node_id: str
    score: float           # combined score (semantic + centrality + freshness)
    distance: float        # raw cosine distance (lower = closer)
    centrality: float      # graph centrality (0–1 normalized)
    freshness: float       # 0..1 forgetting score (1.0 = touched today)
    document: str
    metadata: dict[str, Any]


def _build_where(types: list[str] | None) -> dict[str, Any] | None:
    """Build a ChromaDB `where` filter from node-type constraints."""
    if not types:
        return None
    if len(types) == 1:
        return {"type": types[0]}
    return {"type": {"$in": types}}


def search(
    query: str,
    *,
    types: list[str] | None = None,
    limit: int = SEARCH_DEFAULT_LIMIT,
    centrality_lookup: dict[str, float] | None = None,
    freshness_lookup: dict[str, float] | None = None,
) -> list[SearchHit]:
    """Semantic search across node embeddings, re-ranked by centrality + freshness.

    Args:
        query: Natural-language search string.
        types: Optional list of node types to restrict the search to (e.g. ["CONCEPT"]).
        limit: Max results to return.
        centrality_lookup: Optional `node_id -> centrality_score` mapping (0–1).
            If provided, results are re-ranked. Caller computes this from
            `synapse.graph.operations.compute_centrality()`.
        freshness_lookup: Optional `node_id -> freshness` (0..1) from
            `synapse.graph.freshness.compute_freshness_map()`. Older nodes
            rank lower (PRD Appendix A.3).

    Returns:
        Ranked list of `SearchHit`s, best first.
    """
    if not query.strip():
        return []
    _, coll = _client_and_collection()
    query_vec = embed_text(query)
    overfetch = limit * 3 if (centrality_lookup or freshness_lookup) else limit
    raw = coll.query(
        query_embeddings=[query_vec.tolist()],
        n_results=overfetch,
        where=_build_where(types),
    )

    ids = raw.get("ids", [[]])[0]
    distances = raw.get("distances", [[]])[0]
    documents = raw.get("documents", [[]])[0]
    metadatas = raw.get("metadatas", [[]])[0]

    # Weights — semantic gets whatever is left after centrality + freshness claim their share.
    centrality_w = SEARCH_CENTRALITY_BOOST if centrality_lookup else 0.0
    freshness_w = SEARCH_FRESHNESS_WEIGHT if freshness_lookup else 0.0
    semantic_w = max(0.0, 1.0 - centrality_w - freshness_w)

    hits: list[SearchHit] = []
    for node_id, dist, doc, meta in zip(ids, distances, documents, metadatas, strict=True):
        centrality = (centrality_lookup or {}).get(node_id, 0.0)
        freshness = (freshness_lookup or {}).get(node_id, 1.0)  # missing → assume fresh
        # Cosine distance ∈ [0, 2]; convert to similarity ∈ [-1, 1] then squash to [0, 1].
        semantic = max(0.0, 1.0 - (dist / 2.0))
        combined = semantic * semantic_w + centrality * centrality_w + freshness * freshness_w
        hits.append(
            SearchHit(
                node_id=node_id,
                score=combined,
                distance=dist,
                centrality=centrality,
                freshness=freshness,
                document=doc or "",
                metadata=meta or {},
            )
        )

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


def count_embeddings() -> int:
    """Return the total number of vectors in the collection."""
    _, coll = _client_and_collection()
    return coll.count()
