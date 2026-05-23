"""Search + ChromaDB round-trip tests.

Uses real embeddings and a real PersistentClient pointed at the tmp vault.
"""

from __future__ import annotations

import pytest

from synapse.graph.embeddings import embed_text
from synapse.graph.search import (
    count_embeddings,
    reset_client,
    search,
    upsert_node_embedding,
)


@pytest.fixture(autouse=True)
def _fresh_chroma():  # type: ignore[no-untyped-def]
    """Reset the cached PersistentClient so each test gets its tmp dir's collection."""
    reset_client()
    yield
    reset_client()


def test_upsert_and_count() -> None:
    upsert_node_embedding(
        node_id="n-1",
        text="breadth-first search visits nodes in order of distance",
        metadata={"type": "CONCEPT", "title": "BFS"},
    )
    assert count_embeddings() == 1


def test_search_returns_most_relevant_first() -> None:
    upsert_node_embedding(
        node_id="n-bfs",
        text="breadth-first search BFS shortest path graph traversal",
        metadata={"type": "CONCEPT", "title": "BFS"},
    )
    upsert_node_embedding(
        node_id="n-pasta",
        text="recipes for tomato pasta and italian cuisine",
        metadata={"type": "CONCEPT", "title": "Pasta"},
    )
    hits = search("graph algorithm shortest path", limit=2)
    assert len(hits) == 2
    assert hits[0].node_id == "n-bfs"


def test_search_filters_by_type() -> None:
    upsert_node_embedding(
        node_id="n-concept",
        text="graph theory fundamentals",
        metadata={"type": "CONCEPT", "title": "Graph Theory"},
    )
    upsert_node_embedding(
        node_id="n-build",
        text="implementation of graph traversal in Xtension Signal",
        metadata={"type": "BUILD", "title": "Xtension Signal"},
    )
    concepts = search("graph", types=["CONCEPT"], limit=5)
    assert all(h.metadata["type"] == "CONCEPT" for h in concepts)
    builds = search("graph", types=["BUILD"], limit=5)
    assert all(h.metadata["type"] == "BUILD" for h in builds)


def test_search_empty_query_returns_empty() -> None:
    assert search("") == []
    assert search("   ") == []


def test_search_centrality_boost_promotes_central_nodes() -> None:
    """Two roughly equal hits — the one with higher centrality wins."""
    upsert_node_embedding(
        node_id="n-a",
        text="alpha beta gamma delta",
        metadata={"type": "CONCEPT", "title": "A"},
    )
    upsert_node_embedding(
        node_id="n-b",
        text="alpha beta gamma delta",
        metadata={"type": "CONCEPT", "title": "B"},
    )
    centrality = {"n-a": 0.0, "n-b": 0.9}
    hits = search("alpha", limit=2, centrality_lookup=centrality)
    # With near-identical semantic distance, the centrality boost decides.
    assert hits[0].node_id == "n-b"
