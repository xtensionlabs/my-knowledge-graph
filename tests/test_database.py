"""Tests for database schema + engine setup."""

from __future__ import annotations

from sqlmodel import Session, select

from synapse.graph.db import get_engine, init_db
from synapse.graph.models import CaptureLog, Edge, InboxQueue, Node, NodeType


def test_init_db_idempotent() -> None:
    init_db()
    init_db()  # second call must not error
    engine = get_engine()
    # We should be able to insert + select.
    with Session(engine) as session:
        n = Node(id="test-id", type=NodeType.CONCEPT, title="Graph Theory")
        session.add(n)
        session.commit()

    with Session(engine) as session:
        rows = session.exec(select(Node)).all()
    assert len(rows) == 1
    assert rows[0].title == "Graph Theory"
    assert rows[0].ease_factor == 2.5


def test_edge_round_trip() -> None:
    engine = get_engine()
    with Session(engine) as session:
        session.add(Node(id="a", type=NodeType.CONCEPT, title="A"))
        session.add(Node(id="b", type=NodeType.CONCEPT, title="B"))
        # FK constraint requires nodes to exist before the edge insert.
        # Unit-of-work ordering with column-only FKs doesn't auto-order in
        # SQLModel — flush nodes explicitly.
        session.flush()
        session.add(
            Edge(id="e1", source_node_id="a", target_node_id="b", relation_type="bridges")
        )
        session.commit()
    with Session(engine) as session:
        edges = session.exec(select(Edge)).all()
    assert len(edges) == 1
    assert edges[0].source_node_id == "a"
    assert edges[0].target_node_id == "b"


def test_inbox_queue_and_capture_log_tables_exist() -> None:
    engine = get_engine()
    with Session(engine) as session:
        session.add(InboxQueue(id="q1", source="telegram", payload_json="{}"))
        session.add(CaptureLog(id="c1", source="telegram", inbox_filename="x.md"))
        session.commit()
    with Session(engine) as session:
        assert len(session.exec(select(InboxQueue)).all()) == 1
        assert len(session.exec(select(CaptureLog)).all()) == 1
