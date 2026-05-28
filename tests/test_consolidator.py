"""Tests for Synthesizer.consolidate() — the nightly abstraction pass (PRD Appendix A.2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import Session

from synapse.agents.synthesizer import Synthesizer
from synapse.config import LIBRARIAN_PENDING_INSIGHTS_FILE, get_settings
from synapse.graph import operations
from synapse.graph.db import get_engine
from synapse.graph.models import Node, NodeType


@pytest.fixture(autouse=True)
def _stub_embeddings():  # type: ignore[no-untyped-def]
    with patch.object(operations, "_embed_and_upsert", lambda node: None):
        yield


def _fake_call(parsed):  # type: ignore[no-untyped-def]
    from synapse.llm.client import CallResult

    return CallResult(
        parsed=parsed, raw="<mock>",
        input_tokens=0, output_tokens=0,
        cache_read_tokens=0, cache_creation_tokens=0,
        latency_ms=0, cost_usd=0.0,
    )


def _mock_client(parsed):  # type: ignore[no-untyped-def]
    m = MagicMock()
    m.structured = AsyncMock(return_value=_fake_call(parsed))
    return m


def _seed_fresh_concept(title: str) -> str:
    return operations.create_node(type=NodeType.CONCEPT, title=title, content=f"body of {title}").id


# ── Short-circuit when there aren't enough fresh nodes ───────────────────────


@pytest.mark.asyncio
async def test_consolidate_skips_when_fewer_than_min_fresh_nodes() -> None:
    """With 0 fresh nodes, consolidate should short-circuit and not call Claude."""
    s = Synthesizer(client=_mock_client(None))
    result = await s.consolidate()
    assert result.ok
    assert "skipped" in result.summary
    s._client.structured.assert_not_called()  # type: ignore[attr-defined]


# ── Happy path: ≥3 fresh nodes + LLM proposes ≥1 valid abstraction ────────────


@pytest.mark.asyncio
async def test_consolidate_appends_abstraction_to_pending_insights() -> None:
    _seed_fresh_concept("BFS")
    _seed_fresh_concept("DFS")
    _seed_fresh_concept("Topological sort")

    # Build the consolidator output payload using the same nested types the
    # method declares. We import from the method's local namespace via the
    # class by calling it; instead, build a duck-typed object.
    class _A:
        principle = "Graph algorithms reduce to a shared traversal contract"
        supporting_node_titles = ["BFS", "DFS"]
        domain_bridge = None
        novelty_confidence = 0.9

    class _Out:
        confidence = 0.85
        summary = "Three traversals → one abstraction"
        abstractions = [_A()]

    s = Synthesizer(client=_mock_client(_Out()))
    result = await s.consolidate()
    assert result.ok
    assert result.artifacts["abstractions_proposed"] == 1

    pending_path = Path(result.artifacts["pending_insights_path"])
    text = pending_path.read_text(encoding="utf-8")
    assert "traversal" in text.lower()


# ── Filter: abstractions with <2 supporting nodes are dropped ────────────────


@pytest.mark.asyncio
async def test_consolidate_drops_single_source_abstractions() -> None:
    _seed_fresh_concept("Alpha")
    _seed_fresh_concept("Beta")
    _seed_fresh_concept("Gamma")

    class _SingleA:
        principle = "Single-source insight (invalid)"
        supporting_node_titles = ["Alpha"]  # only one — must be dropped
        domain_bridge = None
        novelty_confidence = 0.9

    class _Out:
        confidence = 0.85
        summary = ""
        abstractions = [_SingleA()]

    s = Synthesizer(client=_mock_client(_Out()))
    result = await s.consolidate()
    assert result.ok
    assert result.artifacts["abstractions_proposed"] == 0


# ── Never auto-creates INSIGHT nodes (CLAUDE.md rule) ────────────────────────


@pytest.mark.asyncio
async def test_consolidate_never_creates_insight_nodes() -> None:
    _seed_fresh_concept("Pi")
    _seed_fresh_concept("Rho")
    _seed_fresh_concept("Sigma")

    class _A:
        principle = "These are all letters"
        supporting_node_titles = ["Pi", "Rho", "Sigma"]
        domain_bridge = None
        novelty_confidence = 0.9

    class _Out:
        confidence = 0.85
        summary = ""
        abstractions = [_A()]

    s = Synthesizer(client=_mock_client(_Out()))
    await s.consolidate()

    # No INSIGHT nodes should exist in the graph.
    with Session(get_engine()) as session:
        from sqlmodel import select
        insights = list(session.exec(select(Node).where(Node.type == NodeType.INSIGHT)).all())
    assert insights == []
