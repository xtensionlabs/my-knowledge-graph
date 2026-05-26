"""Horizon queue tests — manual events + 48h pre-loading."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from synapse.config import HORIZON_ACCELERATED_NEXT_REVIEW_HOURS
from synapse.context.horizon import (
    add_event,
    list_upcoming_events,
    refresh_horizon,
)
from synapse.context.session import get_session
from synapse.graph import operations
from synapse.graph.models import NodeType


@pytest.fixture(autouse=True)
def _stub_embeddings():  # type: ignore[no-untyped-def]
    with patch.object(operations, "_embed_and_upsert", lambda node: None):
        yield


def _seed_concept(title: str, next_review: datetime | None) -> str:
    from sqlmodel import Session
    from synapse.graph.db import get_engine
    from synapse.graph.models import Node

    n = operations.create_node(type=NodeType.CONCEPT, title=title)
    if next_review is not None:
        with Session(get_engine()) as s:
            row = s.get(Node, n.id)
            if row is not None:
                row.next_review = next_review
                s.add(row)
                s.commit()
    return n.id


def test_add_event_creates_event_node_with_encoded_date() -> None:
    when = datetime.now(tz=timezone.utc) + timedelta(days=2)
    event = add_event(title="CAT", date=when)
    assert event.type == NodeType.EVENT
    assert event.title == "CAT"
    # Date is encoded in tags JSON.
    import json
    assert any(t.startswith("_event_date=") for t in json.loads(event.tags))


def test_list_upcoming_events_filters_by_horizon_window() -> None:
    soon = datetime.now(tz=timezone.utc) + timedelta(hours=12)
    far = datetime.now(tz=timezone.utc) + timedelta(days=30)
    add_event(title="Soon Event", date=soon)
    add_event(title="Far Event", date=far)

    upcoming = list_upcoming_events()
    titles = [n.title for n, _ in upcoming]
    assert "Soon Event" in titles
    assert "Far Event" not in titles


def _as_utc(dt: datetime) -> datetime:
    """SQLite drops tzinfo; treat naive returned datetimes as UTC."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def test_event_in_preload_window_accelerates_linked_concepts() -> None:
    """A CONCEPT linked to an event in <48h gets next_review pulled to ≤24h."""
    far_future = datetime.now(tz=timezone.utc) + timedelta(days=30)
    concept_id = _seed_concept("BFS", next_review=far_future)

    soon = datetime.now(tz=timezone.utc) + timedelta(hours=24)  # well inside 48h preload window
    add_event(title="Upcoming CAT", date=soon, linked_concept_titles=["BFS"])
    refresh_horizon()

    n = operations.get_node(concept_id)
    assert n is not None
    assert n.next_review is not None
    cap = datetime.now(tz=timezone.utc) + timedelta(hours=HORIZON_ACCELERATED_NEXT_REVIEW_HOURS + 1)
    assert _as_utc(n.next_review) <= cap


def test_event_outside_preload_window_does_not_accelerate() -> None:
    far_future = datetime.now(tz=timezone.utc) + timedelta(days=30)
    concept_id = _seed_concept("DFS", next_review=far_future)

    later = datetime.now(tz=timezone.utc) + timedelta(hours=60)  # outside 48h window
    add_event(title="Distant CAT", date=later, linked_concept_titles=["DFS"])
    refresh_horizon()

    n = operations.get_node(concept_id)
    assert n is not None
    # Should still be far in the future.
    assert n.next_review is not None
    assert _as_utc(n.next_review) > datetime.now(tz=timezone.utc) + timedelta(days=20)


def test_refresh_horizon_populates_session_state() -> None:
    when = datetime.now(tz=timezone.utc) + timedelta(hours=12)
    add_event(title="X1", date=when)
    n = refresh_horizon()
    assert n >= 1
    snap = get_session()
    assert len(snap.horizon) >= 1
    titles = [h.title for h in snap.horizon]
    assert "X1" in titles
