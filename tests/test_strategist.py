"""Strategist tests — collision detection + LLM-mocked end-to-end."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import Session

from synapse.agents.strategist import (
    Strategist,
    StrategistOutput,
    _CollisionItem,
    _SynergyWindow,
    _TradeoffItem,
    _TradeoffOption,
    detect_collisions,
)
from synapse.context.horizon import add_event
from synapse.graph import operations
from synapse.graph.db import get_engine
from synapse.graph.models import Node, NodeType


@pytest.fixture(autouse=True)
def _stub_embeddings():  # type: ignore[no-untyped-def]
    with patch.object(operations, "_embed_and_upsert", lambda node: None):
        yield


def _fake_call(payload: StrategistOutput):  # type: ignore[no-untyped-def]
    from synapse.llm.client import CallResult

    return CallResult(
        parsed=payload, raw="<mock>",
        input_tokens=0, output_tokens=0,
        cache_read_tokens=0, cache_creation_tokens=0,
        latency_ms=0, cost_usd=0.0,
    )


def _mock_client(out: StrategistOutput):  # type: ignore[no-untyped-def]
    m = MagicMock()
    m.structured = AsyncMock(return_value=_fake_call(out))
    return m


def _seed_concept_with_review(title: str, next_review: datetime) -> str:
    n = operations.create_node(type=NodeType.CONCEPT, title=title, content=f"body {title}")
    with Session(get_engine()) as s:
        row = s.get(Node, n.id)
        if row is not None:
            row.next_review = next_review
            s.add(row)
            s.commit()
    return n.id


# ── Collision detection ──────────────────────────────────────────────────────


def test_detect_collisions_finds_event_concept_overlap() -> None:
    """Event + a CONCEPT whose next_review is within 24h of event date → collision."""
    event_date = datetime.now(tz=timezone.utc) + timedelta(days=2)
    add_event(title="Calculus CAT", date=event_date, linked_concept_titles=["Derivatives"])
    _seed_concept_with_review("Derivatives", event_date + timedelta(hours=4))

    collisions = detect_collisions(lookahead_hours=168)
    assert len(collisions) >= 1
    event_node, concept_nodes = collisions[0]
    assert event_node.title == "Calculus CAT"
    titles = [c.title for c in concept_nodes]
    assert "Derivatives" in titles


def test_detect_collisions_ignores_distant_reviews() -> None:
    """Concept review far from any event date → no collision."""
    event_date = datetime.now(tz=timezone.utc) + timedelta(days=2)
    add_event(title="Discrete CAT", date=event_date, linked_concept_titles=[])
    _seed_concept_with_review("FarFutureConcept", datetime.now(tz=timezone.utc) + timedelta(days=30))

    collisions = detect_collisions(lookahead_hours=168)
    # The event has no overlapping concepts, so either no collision or a tuple with empty concepts.
    for _, concepts in collisions:
        assert "FarFutureConcept" not in [c.title for c in concepts]


# ── End-to-end ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_strategist_writes_strategy_report() -> None:
    out = StrategistOutput(
        confidence=0.85,
        summary="Light week; one collision flagged.",
        collisions=[
            _CollisionItem(
                description="CAT collides with 2 concept reviews",
                event_title="Discrete CAT",
                event_date=datetime.now(tz=timezone.utc).isoformat(),
                concept_titles=["Sets", "Logic"],
                severity="medium",
            )
        ],
        tradeoffs=[
            _TradeoffItem(
                headline="Defer SIGNAL feature shipping to focus on CAT",
                options=[
                    _TradeoffOption(label="Defer feature", cost="1 week of velocity", benefit="2 hours/day to prep"),
                    _TradeoffOption(label="Ship feature", cost="2h/day study", benefit="Investor demo ready"),
                ],
                recommendation="Defer feature",
                reasoning="CAT is closer; demo can wait one week.",
            )
        ],
        synergy_windows=[],
        open_questions_to_resolve=[],
    )
    strat = Strategist(client=_mock_client(out))
    result = await strat.run(lookahead_hours=72)
    assert result.ok
    assert result.artifacts["collision_count"] == 1
    assert result.artifacts["tradeoff_count"] == 1
    path = Path(result.artifacts["report_path"])
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "Discrete CAT" in text
    assert "Defer feature" in text


@pytest.mark.asyncio
async def test_strategist_strengthens_hebbian_edges_on_collisions() -> None:
    """When the Strategist reports a collision over ≥2 concepts, their edges strengthen."""
    # Seed two concepts + an edge between them.
    a = operations.create_node(type=NodeType.CONCEPT, title="Sets")
    b = operations.create_node(type=NodeType.CONCEPT, title="Logic")
    e = operations.create_edge(source_node_id=a.id, target_node_id=b.id, relation_type="applies_to", weight=1.0)
    initial_weight = e.weight

    out = StrategistOutput(
        confidence=0.9,
        summary="One collision",
        collisions=[
            _CollisionItem(
                description="...",
                event_title="CAT",
                event_date=datetime.now(tz=timezone.utc).isoformat(),
                concept_titles=["Sets", "Logic"],
                severity="high",
            )
        ],
    )
    strat = Strategist(client=_mock_client(out))
    await strat.run()

    from synapse.graph.models import Edge as _E
    with Session(get_engine()) as s:
        refreshed = s.get(_E, e.id)
    assert refreshed is not None
    assert refreshed.weight > initial_weight
    assert refreshed.last_strengthened is not None
