"""Critic tests — exactly-one-fix discipline + mocked LLM."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synapse.agents.critic import Critic, CriticOutput, CritiqueRequest, critique_file
from synapse.graph import operations


@pytest.fixture(autouse=True)
def _stub_embeddings():  # type: ignore[no-untyped-def]
    with patch.object(operations, "_embed_and_upsert", lambda node: None):
        yield


def _fake_call(payload: CriticOutput):  # type: ignore[no-untyped-def]
    from synapse.llm.client import CallResult

    return CallResult(
        parsed=payload, raw="<mock>",
        input_tokens=0, output_tokens=0,
        cache_read_tokens=0, cache_creation_tokens=0,
        latency_ms=0, cost_usd=0.0,
    )


def _mock_client(out: CriticOutput):  # type: ignore[no-untyped-def]
    m = MagicMock()
    m.structured = AsyncMock(return_value=_fake_call(out))
    return m


@pytest.mark.asyncio
async def test_critic_returns_single_fix() -> None:
    out = CriticOutput(
        confidence=0.9,
        is_good=False,
        headline="The thesis is buried in paragraph 3",
        diagnosis="Readers won't scroll. Lead with the conclusion.",
        concrete_change="Move sentence beginning 'The key insight' to the top.",
        what_else_you_considered="I considered flagging the missing citations but readability is more load-bearing here.",
    )
    c = Critic(client=_mock_client(out))
    result = await c.run(request=CritiqueRequest(artifact="some draft text here"))
    assert result.ok
    assert result.artifacts["verdict"] == "FIX"
    assert result.artifacts["what_else_you_considered"]  # never empty (the discipline)


@pytest.mark.asyncio
async def test_critic_praises_good_artifact() -> None:
    out = CriticOutput(
        confidence=0.85,
        is_good=True,
        headline="This is good",
        diagnosis="Clear thesis, well-supported, lands the implication.",
        concrete_change="",
        what_else_you_considered="I tried to find a load-bearing fix but the structure is sound.",
    )
    c = Critic(client=_mock_client(out))
    result = await c.run(request=CritiqueRequest(artifact="a good piece of writing"))
    assert result.ok
    assert result.artifacts["verdict"] == "GOOD"


@pytest.mark.asyncio
async def test_critic_rejects_empty_artifact() -> None:
    c = Critic(client=_mock_client(CriticOutput(confidence=0.5, headline="?")))
    result = await c.run(request=CritiqueRequest(artifact=""))
    assert not result.ok
    assert "empty" in result.summary.lower()


@pytest.mark.asyncio
async def test_critic_rejects_oversized_artifact() -> None:
    from synapse.config import CRITIC_MAX_ARTIFACT_BYTES

    huge = "x" * (CRITIC_MAX_ARTIFACT_BYTES + 1)
    c = Critic(client=_mock_client(CriticOutput(confidence=0.5, headline="?")))
    result = await c.run(request=CritiqueRequest(artifact=huge))
    assert not result.ok
    assert "too large" in result.summary.lower()


def test_critique_file_constructor(tmp_path: Path) -> None:
    f = tmp_path / "draft.md"
    f.write_text("body text here", encoding="utf-8")
    req = critique_file(f, kind="prose", context="user note")
    assert req.artifact == "body text here"
    assert req.artifact_kind == "prose"
    assert req.context == "user note"


def test_critic_prompt_file_contains_verbatim_constraint() -> None:
    """CLAUDE.md mandate: 'identify exactly one most important fix — not two, not a list'
    AND 'If you cannot identify a single most important fix, that means the output is
    good. Say so.' must appear verbatim in the prompt.
    """
    prompt_path = Path(__file__).resolve().parent.parent / "synapse" / "prompts" / "critic.md"
    text = prompt_path.read_text(encoding="utf-8")
    assert "identify exactly one 'most important fix' — not two, not a list" in text
    assert "If you cannot identify a single most important fix, that means the output is good. Say so." in text
