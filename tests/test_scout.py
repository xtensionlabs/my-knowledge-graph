"""Scout tests — queue I/O + LLM-mocked digest writing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synapse.agents.scout import (
    SCOUT_QUEUE_FILENAME,
    Scout,
    ScoutItem,
    ScoutOutput,
    _KeptItem,
    _read_queue,
    add_to_queue,
)
from synapse.config import SCOUT_RELEVANCE_THRESHOLD, get_settings
from synapse.graph import operations
from synapse.graph.models import NodeType


@pytest.fixture(autouse=True)
def _stub_embeddings():  # type: ignore[no-untyped-def]
    with patch.object(operations, "_embed_and_upsert", lambda node: None):
        yield


def _fake_call(payload: ScoutOutput):  # type: ignore[no-untyped-def]
    from synapse.llm.client import CallResult

    return CallResult(
        parsed=payload, raw="<mock>",
        input_tokens=0, output_tokens=0,
        cache_read_tokens=0, cache_creation_tokens=0,
        latency_ms=0, cost_usd=0.0,
    )


def _mock_client(out: ScoutOutput):  # type: ignore[no-untyped-def]
    m = MagicMock()
    m.structured = AsyncMock(return_value=_fake_call(out))
    return m


# ── Queue I/O ─────────────────────────────────────────────────────────────────


def test_add_to_queue_appends_item() -> None:
    add_to_queue(ScoutItem(title="Item A", url="http://x", summary="s1"))
    add_to_queue(ScoutItem(title="Item B", url="http://y", summary="s2"))
    settings = get_settings()
    path = settings.synapse_vault_path / "scout" / SCOUT_QUEUE_FILENAME
    items = _read_queue(path, max_items=10)
    assert len(items) == 2
    assert items[0].title == "Item A"
    assert items[1].title == "Item B"


# ── Empty queue short-circuit ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scout_returns_ok_when_queue_empty() -> None:
    s = Scout(client=_mock_client(ScoutOutput(confidence=0.5)))
    result = await s.run()
    assert result.ok
    assert "empty" in result.summary
    s._client.structured.assert_not_called()  # type: ignore[attr-defined]


# ── End-to-end with mocked Claude ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scout_writes_digest_and_clears_queue() -> None:
    operations.create_node(type=NodeType.CONCEPT, title="BFS")
    add_to_queue(ScoutItem(title="A paper on BFS variants", url="http://p", summary="..."))

    out = ScoutOutput(
        confidence=0.8,
        summary="One hit",
        kept=[
            _KeptItem(
                title="A paper on BFS variants",
                url="http://p",
                relevance_score=0.85,
                matches_concepts=["BFS"],
                one_line_why="Direct extension of the user's BFS work; updates a prior.",
            )
        ],
        dropped_count=0,
        drop_reasons_summary="",
    )
    s = Scout(client=_mock_client(out))
    result = await s.run()
    assert result.ok
    assert result.artifacts["kept_count"] == 1

    digest_path = Path(result.artifacts["digest_path"])
    assert digest_path.exists()
    text = digest_path.read_text(encoding="utf-8")
    assert "BFS" in text

    # Queue must be cleared after processing.
    settings = get_settings()
    queue_path = settings.synapse_vault_path / "scout" / SCOUT_QUEUE_FILENAME
    assert _read_queue(queue_path, max_items=10) == []


# ── Threshold gate (server-side enforcement) ──────────────────────────────────


@pytest.mark.asyncio
async def test_scout_drops_below_threshold_items_even_if_llm_kept_them() -> None:
    add_to_queue(ScoutItem(title="Weak item", summary="..."))

    # LLM returns the item kept but with a sub-threshold score; we must drop it.
    weak = _KeptItem(
        title="Weak item",
        relevance_score=SCOUT_RELEVANCE_THRESHOLD - 0.01,
        one_line_why="?",
    )
    out = ScoutOutput(confidence=0.6, summary="meh", kept=[weak], dropped_count=0)
    s = Scout(client=_mock_client(out))
    result = await s.run()
    assert result.ok
    assert result.artifacts["kept_count"] == 0  # threshold gate worked
