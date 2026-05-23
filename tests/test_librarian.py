"""Librarian agent tests.

The ClaudeClient is mocked end-to-end; we test the orchestration around it:
graph mutations, archive behavior, pending files, INSIGHT guard, source_ids.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synapse.agents.librarian import (
    Librarian,
    LibrarianOutput,
    _EdgeProposal,
    _InsightCandidate,
    _NodeProposal,
    _StartupMirror,
)
from synapse.capture.inbox import write_to_inbox
from synapse.config import (
    LIBRARIAN_PENDING_INSIGHTS_FILE,
    LIBRARIAN_PENDING_REVIEW_FILE,
    get_settings,
)
from synapse.graph import operations
from synapse.graph.models import NodeType


@pytest.fixture(autouse=True)
def _stub_embeddings():  # type: ignore[no-untyped-def]
    with patch.object(operations, "_embed_and_upsert", lambda node: None):
        yield


def _fake_call_result(payload: LibrarianOutput):  # type: ignore[no-untyped-def]
    """Wrap a LibrarianOutput as the CallResult the client returns."""
    from synapse.llm.client import CallResult

    return CallResult(
        parsed=payload,
        raw="<mocked>",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        latency_ms=0,
        cost_usd=0.0,
    )


def _seed_inbox(content: str = "test capture body", source: str = "telegram") -> Path:
    """Write a single inbox file and return its path."""
    return write_to_inbox(source=source, content=content)


def _mock_client(outputs: list[LibrarianOutput]):  # type: ignore[no-untyped-def]
    """Build a mocked ClaudeClient that yields canned LibrarianOutput payloads."""
    mock = MagicMock()
    mock.structured = AsyncMock(side_effect=[_fake_call_result(o) for o in outputs])
    return mock


@pytest.mark.asyncio
async def test_librarian_creates_nodes_and_edges_from_one_capture() -> None:
    inbox_path = _seed_inbox()
    output = LibrarianOutput(
        confidence=0.9,
        summary="ok",
        nodes_to_create=[
            _NodeProposal(type="CONCEPT", title="BFS", content="...", confidence=0.9),
            _NodeProposal(
                type="FACT",
                title="BFS shortest paths",
                content="proof sketch",
                confidence=0.85,
            ),
        ],
        edges_to_create=[
            _EdgeProposal(
                source_title="BFS shortest paths",
                target_title="BFS",
                relation="applies_to",
                note="theorem",
            )
        ],
    )
    lib = Librarian(client=_mock_client([output]))
    result = await lib.run()
    assert result.ok is True
    assert result.artifacts["nodes_created"] == 2
    assert result.artifacts["edges_created"] == 1

    # Nodes really exist.
    bfs = operations.find_node_by_title("BFS")
    fact = operations.find_node_by_title("BFS shortest paths")
    assert bfs is not None
    assert fact is not None

    # Edge connects them.
    bundle = operations.get_node_with_edges(bfs.id)
    assert bundle is not None
    incoming_relations = [e.relation_type for e in bundle.in_edges]
    assert "applies_to" in incoming_relations


@pytest.mark.asyncio
async def test_librarian_archives_processed_items_never_deletes() -> None:
    inbox_path = _seed_inbox()
    name = inbox_path.name
    output = LibrarianOutput(confidence=0.1, summary="thin")
    lib = Librarian(client=_mock_client([output]))
    await lib.run()

    # Original inbox file removed.
    assert not inbox_path.exists()

    # Archive copy exists with same name.
    archive_path = get_settings().archive_dir / name
    assert archive_path.exists()
    archived_text = archive_path.read_text(encoding="utf-8")
    assert "processed: true" in archived_text


@pytest.mark.asyncio
async def test_librarian_refuses_to_auto_create_insight_nodes() -> None:
    _seed_inbox()
    output = LibrarianOutput(
        confidence=0.9,
        summary="should not create INSIGHT",
        nodes_to_create=[
            _NodeProposal(
                type="INSIGHT", title="Forbidden Insight", content="x", confidence=0.95
            )
        ],
    )
    lib = Librarian(client=_mock_client([output]))
    await lib.run()

    assert operations.find_node_by_title("Forbidden Insight") is None


@pytest.mark.asyncio
async def test_librarian_writes_insight_candidates_to_pending_file() -> None:
    _seed_inbox()
    output = LibrarianOutput(
        confidence=0.9,
        summary="surfaces an insight candidate",
        insight_candidates=[
            _InsightCandidate(
                description="discrete math = startup proof",
                node_titles=["BFS", "Signal"],
            )
        ],
    )
    lib = Librarian(client=_mock_client([output]))
    await lib.run()

    settings = get_settings()
    pending = settings.synapse_vault_path / LIBRARIAN_PENDING_INSIGHTS_FILE
    assert pending.exists()
    text = pending.read_text(encoding="utf-8")
    assert "discrete math = startup proof" in text


@pytest.mark.asyncio
async def test_librarian_low_confidence_marks_needs_review() -> None:
    _seed_inbox()
    output = LibrarianOutput(
        confidence=0.5,
        summary="weak extraction",
        nodes_to_create=[
            _NodeProposal(
                type="CONCEPT", title="LowConf", content="x", confidence=0.3
            )
        ],
    )
    lib = Librarian(client=_mock_client([output]))
    await lib.run()
    n = operations.find_node_by_title("LowConf")
    assert n is not None
    assert n.needs_review is True


@pytest.mark.asyncio
async def test_librarian_preserves_source_ids() -> None:
    inbox_path = _seed_inbox(content="ledger entry")
    # Find the capture id we just wrote.
    import yaml

    text = inbox_path.read_text(encoding="utf-8")
    end = text.index("\n---\n", 4)
    capture_id = yaml.safe_load(text[4:end])["id"]

    output = LibrarianOutput(
        confidence=0.9,
        summary="ok",
        nodes_to_create=[
            _NodeProposal(type="CONCEPT", title="SourceTrace", content="x", confidence=0.9)
        ],
    )
    lib = Librarian(client=_mock_client([output]))
    await lib.run()

    n = operations.find_node_by_title("SourceTrace")
    assert n is not None
    import json

    assert capture_id in json.loads(n.source_ids)


@pytest.mark.asyncio
async def test_librarian_writes_startup_mirror_suggestions() -> None:
    _seed_inbox()
    output = LibrarianOutput(
        confidence=0.9,
        summary="mirror suggestion",
        startup_mirror_suggestions=[
            _StartupMirror(
                concept_title="BFS",
                build_module="Xtension Signal",
                reason="fan-out uses BFS",
            )
        ],
    )
    lib = Librarian(client=_mock_client([output]))
    await lib.run()
    pending = get_settings().synapse_vault_path / LIBRARIAN_PENDING_REVIEW_FILE
    assert pending.exists()
    assert "Xtension Signal" in pending.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_librarian_concept_gets_initial_sm2_state() -> None:
    _seed_inbox()
    output = LibrarianOutput(
        confidence=0.9,
        summary="ok",
        nodes_to_create=[
            _NodeProposal(type="CONCEPT", title="SM-2 Test", content="x", confidence=0.9)
        ],
    )
    lib = Librarian(client=_mock_client([output]))
    await lib.run()
    n = operations.find_node_by_title("SM-2 Test")
    assert n is not None
    assert n.ease_factor == 2.5
    assert n.next_review is not None


@pytest.mark.asyncio
async def test_librarian_empty_inbox_returns_ok() -> None:
    lib = Librarian(client=_mock_client([]))  # no calls expected
    result = await lib.run()
    assert result.ok is True
    assert result.artifacts["items_processed"] == 0
