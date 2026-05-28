"""Community detection tests — Louvain wrapper + hub identification."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from synapse.graph import operations
from synapse.graph.communities import detect_communities
from synapse.graph.models import NodeType


@pytest.fixture(autouse=True)
def _stub_embeddings():  # type: ignore[no-untyped-def]
    with patch.object(operations, "_embed_and_upsert", lambda node: None):
        yield


def test_empty_graph_returns_no_communities() -> None:
    assert detect_communities() == []


def test_isolated_nodes_dont_form_a_community() -> None:
    """Three orphan nodes with no edges → no community (each is its own singleton)."""
    operations.create_node(type=NodeType.CONCEPT, title="alone1")
    operations.create_node(type=NodeType.CONCEPT, title="alone2")
    operations.create_node(type=NodeType.CONCEPT, title="alone3")
    out = detect_communities(min_size=3)
    # Each isolated node is its own community of size 1; all filtered by min_size.
    assert out == []


def test_two_clusters_emerge() -> None:
    """Build two tightly-connected triangles → expect 2 communities."""
    titles_a = ["a1", "a2", "a3"]
    titles_b = ["b1", "b2", "b3"]
    nodes_a = [operations.create_node(type=NodeType.CONCEPT, title=t) for t in titles_a]
    nodes_b = [operations.create_node(type=NodeType.CONCEPT, title=t) for t in titles_b]

    # Triangle A
    for i in range(3):
        for j in range(i + 1, 3):
            operations.create_edge(
                source_node_id=nodes_a[i].id, target_node_id=nodes_a[j].id,
                relation_type="applies_to", weight=3.0,
            )
    # Triangle B
    for i in range(3):
        for j in range(i + 1, 3):
            operations.create_edge(
                source_node_id=nodes_b[i].id, target_node_id=nodes_b[j].id,
                relation_type="applies_to", weight=3.0,
            )

    communities = detect_communities(min_size=3)
    assert len(communities) >= 2
    sizes = {c.size for c in communities}
    assert 3 in sizes


def test_hub_concepts_are_highest_degree_in_community() -> None:
    """In a star (center connected to 4 leaves), the center should be the top hub."""
    center = operations.create_node(type=NodeType.CONCEPT, title="hub")
    leaves = [
        operations.create_node(type=NodeType.CONCEPT, title=f"leaf{i}")
        for i in range(4)
    ]
    for leaf in leaves:
        operations.create_edge(
            source_node_id=center.id, target_node_id=leaf.id,
            relation_type="applies_to", weight=1.0,
        )

    communities = detect_communities(min_size=3, hub_top_k=1)
    assert len(communities) >= 1
    # Find the community containing the hub
    hub_community = next((c for c in communities if center.id in c.node_ids), None)
    assert hub_community is not None
    assert hub_community.hubs[0].node_id == center.id
    assert hub_community.hubs[0].title == "hub"


def test_communities_sorted_by_size_descending() -> None:
    """Larger communities come first in the result."""
    # 4-clique
    big = [operations.create_node(type=NodeType.CONCEPT, title=f"big{i}") for i in range(4)]
    for i in range(4):
        for j in range(i + 1, 4):
            operations.create_edge(
                source_node_id=big[i].id, target_node_id=big[j].id,
                relation_type="applies_to", weight=3.0,
            )
    # Triangle
    small = [operations.create_node(type=NodeType.CONCEPT, title=f"sm{i}") for i in range(3)]
    for i in range(3):
        for j in range(i + 1, 3):
            operations.create_edge(
                source_node_id=small[i].id, target_node_id=small[j].id,
                relation_type="applies_to", weight=3.0,
            )

    communities = detect_communities(min_size=3)
    sizes = [c.size for c in communities]
    assert sizes == sorted(sizes, reverse=True)
