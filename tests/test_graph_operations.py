"""Graph CRUD + traversal tests. Embeddings + ChromaDB are stubbed."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from synapse.graph import operations
from synapse.graph.models import NodeType


@pytest.fixture(autouse=True)
def _stub_embeddings():  # type: ignore[no-untyped-def]
    """Replace heavy embed/upsert with no-ops so tests stay fast."""
    with patch.object(operations, "_embed_and_upsert", lambda node: None):
        yield


def test_create_node_returns_node_with_obsidian_path() -> None:
    node = operations.create_node(
        type="CONCEPT", title="Graph Theory", content="study of graphs"
    )
    assert node.title == "Graph Theory"
    assert node.type == NodeType.CONCEPT
    assert node.obsidian_path.startswith("concepts/")
    assert node.id


def test_create_node_dedup_returns_existing() -> None:
    a = operations.create_node(type="CONCEPT", title="BFS")
    b = operations.create_node(type="CONCEPT", title="bfs")  # case-insensitive
    assert a.id == b.id


def test_create_node_dedup_disabled_raises_on_duplicate() -> None:
    operations.create_node(type="CONCEPT", title="DFS")
    with pytest.raises(operations.DuplicateNodeError):
        operations.create_node(type="CONCEPT", title="DFS", dedup=False)


def test_create_node_rejects_invalid_type() -> None:
    with pytest.raises(operations.GraphError):
        operations.create_node(type="BLAH", title="x")


def test_update_node_appends_content() -> None:
    n = operations.create_node(type="CONCEPT", title="SM-2", content="initial body")
    updated = operations.update_node(n.id, content_addition="more detail here")
    assert "initial body" in updated.content
    assert "more detail here" in updated.content


def test_update_node_merges_source_ids_and_tags() -> None:
    n = operations.create_node(
        type="CONCEPT", title="Routing", source_ids=["cap-1"], tags=["networks"]
    )
    updated = operations.update_node(
        n.id, new_source_ids=["cap-2"], new_tags=["cs-fundamentals"]
    )
    import json

    assert set(json.loads(updated.source_ids)) == {"cap-1", "cap-2"}
    assert set(json.loads(updated.tags)) == {"networks", "cs-fundamentals"}


def test_create_edge_validates_relation() -> None:
    a = operations.create_node(type="CONCEPT", title="A-edge-test")
    b = operations.create_node(type="CONCEPT", title="B-edge-test")
    with pytest.raises(operations.UnknownRelationError):
        operations.create_edge(
            source_node_id=a.id, target_node_id=b.id, relation_type="invented_relation"
        )


def test_create_edge_dedup() -> None:
    a = operations.create_node(type="CONCEPT", title="A2")
    b = operations.create_node(type="CONCEPT", title="B2")
    e1 = operations.create_edge(
        source_node_id=a.id, target_node_id=b.id, relation_type="bridges"
    )
    e2 = operations.create_edge(
        source_node_id=a.id, target_node_id=b.id, relation_type="bridges"
    )
    assert e1.id == e2.id


def test_create_edge_rejects_self_loop() -> None:
    a = operations.create_node(type="CONCEPT", title="Loopy")
    with pytest.raises(operations.GraphError):
        operations.create_edge(
            source_node_id=a.id, target_node_id=a.id, relation_type="bridges"
        )


def test_create_edge_requires_existing_endpoints() -> None:
    a = operations.create_node(type="CONCEPT", title="Real-node")
    with pytest.raises(operations.GraphError):
        operations.create_edge(
            source_node_id=a.id,
            target_node_id="missing-id",
            relation_type="bridges",
        )


def test_find_orphans_detects_isolated_nodes() -> None:
    operations.create_node(type="CONCEPT", title="Lonely")
    a = operations.create_node(type="CONCEPT", title="Connected-A")
    b = operations.create_node(type="CONCEPT", title="Connected-B")
    operations.create_edge(
        source_node_id=a.id, target_node_id=b.id, relation_type="bridges"
    )
    orphans = operations.find_orphans()
    assert len(orphans) == 1
    assert orphans[0].title == "Lonely"


def test_graph_stats_counts_correctly() -> None:
    operations.create_node(type="CONCEPT", title="S1")
    operations.create_node(type="BUILD", title="S2")
    a = operations.create_node(type="CONCEPT", title="S3")
    b = operations.create_node(type="CONCEPT", title="S4")
    operations.create_edge(
        source_node_id=a.id, target_node_id=b.id, relation_type="bridges"
    )

    stats = operations.graph_stats()
    assert stats["node_count"] == 4
    assert stats["edge_count"] == 1
    assert stats["nodes_by_type"]["CONCEPT"] == 3
    assert stats["nodes_by_type"]["BUILD"] == 1


def test_list_nodes_summary_returns_lightweight_view() -> None:
    operations.create_node(type="CONCEPT", title="X1", tags=["t1", "t2"])
    operations.create_node(type="BUILD", title="X2")
    summary = operations.list_nodes_summary()
    assert len(summary) == 2
    titles = {s.title for s in summary}
    assert titles == {"X1", "X2"}
