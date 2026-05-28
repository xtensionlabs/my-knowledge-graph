"""Tests for synapse/graph/hebbian.py — strengthen / decay / weak-edge surfacing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlmodel import Session

from synapse.config import (
    HEBBIAN_STRENGTHEN_FACTOR,
    HEBBIAN_WEIGHT_CEILING,
    HEBBIAN_WEIGHT_FLOOR,
)
from synapse.graph import operations
from synapse.graph.db import get_engine
from synapse.graph.hebbian import (
    decay_old_edges,
    list_weak_edges,
    strengthen_edges,
)
from synapse.graph.models import Edge, NodeType


@pytest.fixture(autouse=True)
def _stub_embeddings():  # type: ignore[no-untyped-def]
    with patch.object(operations, "_embed_and_upsert", lambda node: None):
        yield


def _seed_pair() -> tuple[str, str, str]:
    """Create two CONCEPT nodes + one edge between them; return (a_id, b_id, edge_id)."""
    a = operations.create_node(type=NodeType.CONCEPT, title="A_concept")
    b = operations.create_node(type=NodeType.CONCEPT, title="B_concept")
    e = operations.create_edge(
        source_node_id=a.id, target_node_id=b.id, relation_type="applies_to", weight=1.0
    )
    return a.id, b.id, e.id


# ── strengthen_edges ─────────────────────────────────────────────────────────


def test_strengthen_bumps_edge_weight() -> None:
    a_id, b_id, e_id = _seed_pair()
    result = strengthen_edges([a_id, b_id])
    assert result.edges_strengthened == 1
    with Session(get_engine()) as s:
        edge = s.get(Edge, e_id)
        assert edge is not None
        assert edge.weight == pytest.approx(1.0 + HEBBIAN_STRENGTHEN_FACTOR)
        assert edge.last_strengthened is not None


def test_strengthen_ignored_with_fewer_than_two_ids() -> None:
    a_id, _, _ = _seed_pair()
    result = strengthen_edges([a_id])
    assert result.edges_strengthened == 0


def test_strengthen_capped_at_ceiling() -> None:
    a_id, b_id, e_id = _seed_pair()
    # Force weight close to ceiling first.
    with Session(get_engine()) as s:
        e = s.get(Edge, e_id)
        e.weight = HEBBIAN_WEIGHT_CEILING - 0.01
        s.add(e)
        s.commit()
    strengthen_edges([a_id, b_id])
    with Session(get_engine()) as s:
        e = s.get(Edge, e_id)
        assert e.weight == pytest.approx(HEBBIAN_WEIGHT_CEILING)


def test_strengthen_only_edges_with_both_endpoints_in_set() -> None:
    """A node in the set but whose partner is OUT of the set is NOT strengthened."""
    a_id, b_id, e_id = _seed_pair()
    c = operations.create_node(type=NodeType.CONCEPT, title="C_concept")
    operations.create_edge(source_node_id=a_id, target_node_id=c.id, relation_type="applies_to", weight=1.0)

    result = strengthen_edges([a_id, b_id])  # c NOT in set
    assert result.edges_strengthened == 1  # only the a–b edge

    # Verify c-edge untouched
    with Session(get_engine()) as s:
        from sqlmodel import select
        edges = list(s.exec(select(Edge).where(Edge.target_node_id == c.id)).all())
        assert all(e.weight == 1.0 for e in edges)


# ── decay_old_edges ──────────────────────────────────────────────────────────


def test_decay_skips_recently_strengthened() -> None:
    a_id, b_id, e_id = _seed_pair()
    strengthen_edges([a_id, b_id])  # last_strengthened = now
    before_weight = None
    with Session(get_engine()) as s:
        before_weight = s.get(Edge, e_id).weight
    decay_old_edges()
    with Session(get_engine()) as s:
        assert s.get(Edge, e_id).weight == before_weight  # unchanged


def test_decay_reduces_old_edges() -> None:
    a_id, b_id, e_id = _seed_pair()
    # Set last_strengthened to long ago.
    with Session(get_engine()) as s:
        e = s.get(Edge, e_id)
        e.last_strengthened = datetime.now(tz=timezone.utc) - timedelta(days=30)
        s.add(e)
        s.commit()
        old_weight = e.weight

    result = decay_old_edges()
    assert result.edges_decayed >= 1

    with Session(get_engine()) as s:
        new_weight = s.get(Edge, e_id).weight
    assert new_weight < old_weight


def test_decay_floors_at_minimum() -> None:
    a_id, b_id, e_id = _seed_pair()
    # Start below the floor so a single decay multiplication clamps up to the floor.
    with Session(get_engine()) as s:
        e = s.get(Edge, e_id)
        e.weight = HEBBIAN_WEIGHT_FLOOR - 0.001  # already under floor
        e.last_strengthened = datetime.now(tz=timezone.utc) - timedelta(days=365)
        s.add(e)
        s.commit()
    decay_old_edges()
    with Session(get_engine()) as s:
        assert s.get(Edge, e_id).weight == pytest.approx(HEBBIAN_WEIGHT_FLOOR)


def test_decay_uses_created_at_when_last_strengthened_null() -> None:
    """Legacy rows have last_strengthened=None — decay should fall back to created_at."""
    a_id, b_id, e_id = _seed_pair()
    # last_strengthened is None on freshly created edges (the seed pair created
    # via operations.create_edge does not set it). Push created_at into the past.
    with Session(get_engine()) as s:
        e = s.get(Edge, e_id)
        e.created_at = datetime.now(tz=timezone.utc) - timedelta(days=30)
        s.add(e)
        s.commit()
    result = decay_old_edges()
    assert result.edges_decayed >= 1


# ── list_weak_edges ──────────────────────────────────────────────────────────


def test_weak_edges_surfaced_but_not_deleted() -> None:
    a_id, b_id, e_id = _seed_pair()
    with Session(get_engine()) as s:
        e = s.get(Edge, e_id)
        e.weight = 0.05
        s.add(e)
        s.commit()

    weak = list_weak_edges(threshold=0.1)
    assert any(w.id == e_id for w in weak)

    # CRITICAL: edge still exists (CLAUDE.md rule — never auto-delete).
    with Session(get_engine()) as s:
        assert s.get(Edge, e_id) is not None
