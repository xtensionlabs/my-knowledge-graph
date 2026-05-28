"""Guardian tests — short-circuits + LLM-mocked nudge writing."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import Session

from synapse.agents.guardian import (
    Guardian,
    GuardianOutput,
    NUDGES_FILENAME,
    _enforce_line_cap,
    gather_signals,
)
from synapse.config import (
    GUARDIAN_CAPTURE_QUALITY_MIN_AVG_BYTES,
    GUARDIAN_RETENTION_LAPSE_THRESHOLD,
    get_settings,
)
from synapse.graph import operations
from synapse.graph.db import get_engine
from synapse.graph.models import CaptureLog, Node, NodeType


@pytest.fixture(autouse=True)
def _stub_embeddings():  # type: ignore[no-untyped-def]
    with patch.object(operations, "_embed_and_upsert", lambda node: None):
        yield


def _fake_call(payload: GuardianOutput):  # type: ignore[no-untyped-def]
    from synapse.llm.client import CallResult

    return CallResult(
        parsed=payload, raw="<mock>",
        input_tokens=0, output_tokens=0,
        cache_read_tokens=0, cache_creation_tokens=0,
        latency_ms=0, cost_usd=0.0,
    )


def _mock_client(out: GuardianOutput):  # type: ignore[no-untyped-def]
    m = MagicMock()
    m.structured = AsyncMock(return_value=_fake_call(out))
    return m


def _seed_overdue_concepts(n: int) -> None:
    for i in range(n):
        node = operations.create_node(
            type=NodeType.CONCEPT, title=f"OverdueConcept{i}"
        )
        with Session(get_engine()) as s:
            row = s.get(Node, node.id)
            row.next_review = datetime.now(tz=timezone.utc) - timedelta(hours=12)
            s.add(row)
            s.commit()


def _seed_tiny_captures(n: int) -> None:
    """Insert N capture_log rows with tiny size_bytes — triggers quality threshold."""
    with Session(get_engine()) as s:
        for i in range(n):
            s.add(
                CaptureLog(
                    id=str(uuid.uuid4()),
                    source="manual",
                    inbox_filename=f"tiny-{i}.md",
                    created_at=datetime.now(tz=timezone.utc) - timedelta(minutes=10 + i),
                    size_bytes=10,  # << GUARDIAN_CAPTURE_QUALITY_MIN_AVG_BYTES
                )
            )
        s.commit()


# ── Signal aggregation ────────────────────────────────────────────────────────


def test_gather_signals_detects_capture_quality_threshold() -> None:
    _seed_tiny_captures(5)
    sig = gather_signals()
    assert sig.avg_size_bytes < GUARDIAN_CAPTURE_QUALITY_MIN_AVG_BYTES
    assert "capture_quality" in sig.threshold_fired


def test_gather_signals_detects_retention_lapses() -> None:
    _seed_overdue_concepts(GUARDIAN_RETENTION_LAPSE_THRESHOLD + 1)
    sig = gather_signals()
    assert sig.overdue_count >= GUARDIAN_RETENTION_LAPSE_THRESHOLD
    assert "retention_lapse" in sig.threshold_fired


# ── Short-circuit: no thresholds → silent without LLM ─────────────────────────


@pytest.mark.asyncio
async def test_guardian_silent_when_no_thresholds() -> None:
    g = Guardian(client=_mock_client(GuardianOutput(nudge=False)))
    result = await g.run()
    assert result.ok
    assert "silent" in result.summary
    g._client.structured.assert_not_called()  # type: ignore[attr-defined]


# ── End-to-end: threshold fires → LLM called → nudge written ──────────────────


@pytest.mark.asyncio
async def test_guardian_writes_nudge_when_threshold_fires() -> None:
    _seed_overdue_concepts(GUARDIAN_RETENTION_LAPSE_THRESHOLD + 1)
    out = GuardianOutput(
        nudge=True,
        reason="retention_lapse",
        message="You have a backlog. Maybe cut today's scope to one concept.",
        scope_suggestion="Focus on the oldest CAT topic only.",
        confidence=0.85,
    )
    g = Guardian(client=_mock_client(out))
    result = await g.run()
    assert result.ok
    assert result.artifacts["nudge"] is True

    nudges_path = Path(result.artifacts["nudges_path"])
    assert nudges_path.is_file()
    text = nudges_path.read_text(encoding="utf-8")
    assert "retention_lapse" in text
    assert "backlog" in text


# ── Line cap enforcement ──────────────────────────────────────────────────────


def test_enforce_line_cap_truncates_above_max() -> None:
    long = "line1\nline2\nline3\nline4"
    capped = _enforce_line_cap(long, max_lines=2)
    assert len(capped.splitlines()) == 2
    assert capped.splitlines()[-1].endswith("…")


def test_enforce_line_cap_leaves_short_alone() -> None:
    assert _enforce_line_cap("hi\nthere", max_lines=2) == "hi\nthere"


# ── Cooldown ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_guardian_respects_cooldown() -> None:
    """If guardian_nudges.md was just touched, Guardian stays silent."""
    settings = get_settings()
    nudges_path = settings.synapse_vault_path / "daily" / NUDGES_FILENAME
    nudges_path.parent.mkdir(parents=True, exist_ok=True)
    nudges_path.write_text("## just now\n\nsomething\n", encoding="utf-8")

    _seed_overdue_concepts(GUARDIAN_RETENTION_LAPSE_THRESHOLD + 1)  # threshold fires
    g = Guardian(client=_mock_client(GuardianOutput(nudge=True, message="should not fire")))
    result = await g.run()
    assert "cooldown" in result.summary
    g._client.structured.assert_not_called()  # type: ignore[attr-defined]
