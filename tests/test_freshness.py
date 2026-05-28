"""Tests for synapse/graph/freshness.py — derived freshness scoring."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlmodel import Session

from synapse.graph import operations
from synapse.graph.db import get_engine
from synapse.graph.freshness import (
    compute_freshness_map,
    list_cold_nodes,
)
from synapse.graph.models import Node, NodeType


@pytest.fixture(autouse=True)
def _stub_embeddings():  # type: ignore[no-untyped-def]
    with patch.object(operations, "_embed_and_upsert", lambda node: None):
        yield


def _backdate_node(node_id: str, *, days_old: int) -> None:
    """Push a node's created_at and updated_at into the past."""
    when = datetime.now(tz=timezone.utc) - timedelta(days=days_old)
    with Session(get_engine()) as s:
        n = s.get(Node, node_id)
        if n is not None:
            n.created_at = when
            n.updated_at = when
            s.add(n)
            s.commit()


def test_fresh_node_has_score_close_to_one() -> None:
    n = operations.create_node(type=NodeType.CONCEPT, title="just-created")
    fresh = compute_freshness_map()
    assert fresh[n.id] > 0.95


def test_old_node_has_low_score() -> None:
    n = operations.create_node(type=NodeType.CONCEPT, title="ancient")
    _backdate_node(n.id, days_old=150)
    fresh = compute_freshness_map()
    assert fresh[n.id] < 0.5


def test_node_beyond_horizon_floors_at_zero() -> None:
    n = operations.create_node(type=NodeType.CONCEPT, title="forgotten")
    _backdate_node(n.id, days_old=400)  # > FORGETTING_HORIZON_DAYS
    fresh = compute_freshness_map()
    assert fresh[n.id] == 0.0


def test_list_cold_nodes_returns_below_threshold() -> None:
    a = operations.create_node(type=NodeType.CONCEPT, title="cold-a")
    b = operations.create_node(type=NodeType.CONCEPT, title="warm-b")
    _backdate_node(a.id, days_old=300)
    # b stays fresh
    cold = list_cold_nodes(threshold=0.1)
    cold_ids = {n.id for n, _ in cold}
    assert a.id in cold_ids
    assert b.id not in cold_ids


def test_freshness_respects_edge_strengthening() -> None:
    """A cold node whose edge was just strengthened should bump back toward fresh."""
    from synapse.graph.hebbian import strengthen_edges
    from synapse.graph.models import Edge as _E

    a = operations.create_node(type=NodeType.CONCEPT, title="cold-but-rewired-a")
    b = operations.create_node(type=NodeType.CONCEPT, title="cold-but-rewired-b")
    edge = operations.create_edge(source_node_id=a.id, target_node_id=b.id, relation_type="applies_to")
    _backdate_node(a.id, days_old=200)
    _backdate_node(b.id, days_old=200)
    # Also backdate the edge itself so it doesn't keep these nodes "warm" via the edge timestamp.
    long_ago = datetime.now(tz=timezone.utc) - timedelta(days=200)
    with Session(get_engine()) as s:
        e_row = s.get(_E, edge.id)
        e_row.created_at = long_ago
        e_row.last_strengthened = None
        s.add(e_row)
        s.commit()

    # Before strengthening — both cold.
    before = compute_freshness_map()
    assert before[a.id] < 0.5
    assert before[b.id] < 0.5

    strengthen_edges([a.id, b.id])

    after = compute_freshness_map()
    # After strengthening — both nodes get a recency boost via the edge timestamp.
    assert after[a.id] > before[a.id]
    assert after[b.id] > before[b.id]
