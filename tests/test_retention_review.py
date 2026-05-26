"""Tests for the review API: get_due_reviews, apply_rating, push_question."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from synapse.graph import operations
from synapse.graph.models import NodeType
from synapse.graph.retention import (
    apply_rating,
    get_due_reviews,
    push_question,
)


@pytest.fixture(autouse=True)
def _stub_embeddings():  # type: ignore[no-untyped-def]
    with patch.object(operations, "_embed_and_upsert", lambda node: None):
        yield


def _seed_concept(title: str, next_review: datetime | None) -> str:
    """Helper: create a CONCEPT with explicit next_review."""
    from sqlmodel import Session
    from synapse.graph.db import get_engine
    from synapse.graph.models import Node

    n = operations.create_node(type=NodeType.CONCEPT, title=title, content=f"body of {title}")
    with Session(get_engine()) as s:
        row = s.get(Node, n.id)
        if row is not None:
            row.next_review = next_review
            s.add(row)
            s.commit()
    return n.id


def test_get_due_reviews_returns_only_overdue() -> None:
    past = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    future = datetime.now(tz=timezone.utc) + timedelta(days=2)
    a = _seed_concept("Due Now", past)
    b = _seed_concept("Due Later", future)

    due = get_due_reviews(limit=10)
    ids = [d.node_id for d in due]
    assert a in ids
    assert b not in ids


def test_get_due_reviews_orders_by_oldest_first() -> None:
    older = datetime.now(tz=timezone.utc) - timedelta(days=3)
    newer = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    a = _seed_concept("Older", older)
    b = _seed_concept("Newer", newer)

    due = get_due_reviews(limit=10)
    ids = [d.node_id for d in due]
    assert ids.index(a) < ids.index(b)


def test_apply_rating_updates_sm2_state() -> None:
    past = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    cid = _seed_concept("Rate Me", past)

    state = apply_rating(node_id=cid, quality=4)
    assert state.review_count == 1
    assert state.next_review > datetime.now(tz=timezone.utc)

    # Persisted in DB. SQLite drops tzinfo, so compare naive-to-naive.
    n = operations.get_node(cid)
    assert n is not None
    assert n.review_count == 1
    assert n.next_review is not None
    persisted = (
        n.next_review.replace(tzinfo=timezone.utc) if n.next_review.tzinfo is None
        else n.next_review
    )
    assert persisted > datetime.now(tz=timezone.utc)


def test_apply_rating_rejects_non_concept() -> None:
    n = operations.create_node(type=NodeType.BUILD, title="Some Build")
    with pytest.raises(ValueError):
        apply_rating(node_id=n.id, quality=4)


def test_apply_rating_rejects_unknown_node() -> None:
    with pytest.raises(ValueError):
        apply_rating(node_id="not-an-id", quality=3)


def test_apply_rating_rejects_out_of_range_quality() -> None:
    cid = _seed_concept("Q", datetime.now(tz=timezone.utc) - timedelta(hours=1))
    with pytest.raises(ValueError):
        apply_rating(node_id=cid, quality=0)
    with pytest.raises(ValueError):
        apply_rating(node_id=cid, quality=6)


def test_push_question_rotates_bank() -> None:
    cid = _seed_concept("Q-bank", datetime.now(tz=timezone.utc) - timedelta(hours=1))
    for i in range(7):
        push_question(cid, f"Q{i}: scenario {i}")
    n = operations.get_node(cid)
    assert n is not None
    import json

    bank = json.loads(n.review_questions)
    # Bank capped at SYNTHESIZER_QUESTION_BANK_MAX (5)
    assert len(bank) == 5
    assert bank[-1] == "Q6: scenario 6"
    assert "Q0: scenario 0" not in bank


def test_push_question_dedups_consecutive() -> None:
    cid = _seed_concept("Q-dedup", datetime.now(tz=timezone.utc) - timedelta(hours=1))
    push_question(cid, "same")
    push_question(cid, "same")
    n = operations.get_node(cid)
    assert n is not None
    import json
    assert json.loads(n.review_questions) == ["same"]
