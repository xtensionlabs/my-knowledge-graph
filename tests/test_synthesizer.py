"""Synthesizer tests — mocked Claude, real DB."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synapse.agents.synthesizer import (
    Synthesizer,
    SynthesizerOutput,
    _Bridge,
    _HorizonPrep,
    _OpenQuestion,
    _RetentionAlert,
    render_delta_briefing,
)
from synapse.config import SYNTHESIZER_DAILY_FILE_FORMAT, get_settings
from synapse.graph import operations
from synapse.graph.models import NodeType


@pytest.fixture(autouse=True)
def _stub_embeddings():  # type: ignore[no-untyped-def]
    with patch.object(operations, "_embed_and_upsert", lambda node: None):
        yield


def _fake_call(payload: SynthesizerOutput):  # type: ignore[no-untyped-def]
    from synapse.llm.client import CallResult

    return CallResult(
        parsed=payload, raw="<mock>",
        input_tokens=0, output_tokens=0,
        cache_read_tokens=0, cache_creation_tokens=0,
        latency_ms=0, cost_usd=0.0,
    )


def _mock_client(out: SynthesizerOutput):  # type: ignore[no-untyped-def]
    m = MagicMock()
    m.structured = AsyncMock(return_value=_fake_call(out))
    return m


def _seed_overdue_concept(title: str) -> str:
    from sqlmodel import Session
    from synapse.graph.db import get_engine
    from synapse.graph.models import Node

    n = operations.create_node(type=NodeType.CONCEPT, title=title, content=f"body of {title}")
    with Session(get_engine()) as s:
        row = s.get(Node, n.id)
        if row is not None:
            row.next_review = datetime.now(tz=timezone.utc) - timedelta(hours=2)
            s.add(row)
            s.commit()
    return n.id


@pytest.mark.asyncio
async def test_synthesizer_writes_daily_brief_file() -> None:
    out = SynthesizerOutput(
        confidence=0.9,
        retention_alerts=[],
        horizon_prep=[],
        bridge=None,
        open_question=None,
        summary_line="quiet day",
    )
    syn = Synthesizer(client=_mock_client(out))
    result = await syn.run()
    assert result.ok
    daily_path = result.artifacts["daily_path"]
    from pathlib import Path
    assert Path(daily_path).exists()
    text = Path(daily_path).read_text(encoding="utf-8")
    assert "Delta Briefing" in text
    assert "quiet day" in text


@pytest.mark.asyncio
async def test_synthesizer_pushes_questions_to_concept_banks() -> None:
    cid = _seed_overdue_concept("BFS Test")
    out = SynthesizerOutput(
        confidence=0.9,
        retention_alerts=[
            _RetentionAlert(
                node_id=cid,
                title="BFS Test",
                application_question="Design a fan-out using BFS — what changes if edge weights matter?",
                why_now="due today",
            )
        ],
    )
    syn = Synthesizer(client=_mock_client(out))
    result = await syn.run()
    assert result.ok
    assert result.artifacts["questions_pushed"] == 1

    # Verify the question is now in the concept's bank.
    import json
    n = operations.get_node(cid)
    assert n is not None
    bank = json.loads(n.review_questions)
    assert any("fan-out" in q for q in bank)


@pytest.mark.asyncio
async def test_synthesizer_renders_all_brief_sections() -> None:
    out = SynthesizerOutput(
        confidence=0.9,
        retention_alerts=[
            _RetentionAlert(
                node_id="x", title="A", application_question="Q?", why_now="W"
            )
        ],
        horizon_prep=[
            _HorizonPrep(
                event_node_id="y", event_title="CAT", hours_until=24,
                prep_summary="study BFS", prep_concept_titles=["BFS"]
            )
        ],
        bridge=_Bridge(
            headline="connecting idea",
            academic_anchor="BFS",
            startup_anchor="Signal",
            reasoning="because reasons.",
            confidence=0.8,
        ),
        open_question=_OpenQuestion(
            node_id="q", title="Open?", prompt="think about it"
        ),
        summary_line="all sections present",
    )
    md = render_delta_briefing(out, date_iso="2026-05-23")
    assert "Retention Alerts" in md
    assert "Horizon Prep" in md
    assert "Bridge" in md
    assert "Open Question" in md
    assert "all sections present" in md
    assert "CAT" in md
    assert "connecting idea" in md


def test_render_delta_briefing_handles_empty_brief() -> None:
    out = SynthesizerOutput(
        confidence=0.95, retention_alerts=[], horizon_prep=[],
        bridge=None, open_question=None, summary_line="quiet"
    )
    md = render_delta_briefing(out, date_iso="2026-05-23")
    assert "no overdue reviews" in md
    assert "no events" in md
    assert "no high-confidence bridge" in md
    assert "no stale open questions" in md
