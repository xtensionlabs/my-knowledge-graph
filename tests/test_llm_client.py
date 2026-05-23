"""LLM client wrapper tests — Anthropic SDK is mocked end-to-end."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from synapse.config import LIBRARIAN_MODEL
from synapse.graph.db import get_engine
from synapse.graph.models import ApiUsage
from synapse.llm.client import ClaudeClient, StructuredOutputError


class TinyOut(BaseModel):
    """Minimal schema for client tests."""

    name: str
    count: int


@dataclass
class FakeUsage:
    input_tokens: int = 100
    output_tokens: int = 20
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None


def _fake_response(text: str, usage: FakeUsage | None = None) -> Any:
    """Build a fake Anthropic response object."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.content = [block]
    response.usage = usage or FakeUsage()
    return response


@pytest.fixture
def prompts_dir(monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    """Override prompts dir with a tmp directory + a minimal template."""
    pdir = tmp_path / "prompts"
    pdir.mkdir()
    (pdir / "tiny.md").write_text("name={{ name }} count={{ count }}", encoding="utf-8")
    # Patch Settings.prompts_dir via the lru_cache breaker.
    from synapse.config import Settings, reset_settings_cache

    reset_settings_cache()
    monkeypatch.setattr(Settings, "prompts_dir", property(lambda self: pdir))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    reset_settings_cache()
    return pdir


@pytest.mark.asyncio
async def test_structured_call_happy_path(prompts_dir, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Valid JSON, schema matches → parsed payload + usage row written."""
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(
        return_value=_fake_response('{"name": "alpha", "count": 7}')
    )

    client = ClaudeClient()
    monkeypatch.setattr(client, "_ensure_client", lambda: fake_client)

    result = await client.structured(
        prompt_file="tiny.md",
        context={"name": "x", "count": 1},
        schema=TinyOut,
        model=LIBRARIAN_MODEL,
        agent="librarian",
    )
    assert isinstance(result.parsed, TinyOut)
    assert result.parsed.name == "alpha"
    assert result.parsed.count == 7
    assert result.input_tokens == 100
    assert result.output_tokens == 20

    # api_usage row written.
    from sqlmodel import Session, select

    with Session(get_engine()) as session:
        rows = session.exec(select(ApiUsage).where(ApiUsage.agent == "librarian")).all()
    assert len(rows) == 1
    assert rows[0].succeeded is True
    assert rows[0].model == LIBRARIAN_MODEL


@pytest.mark.asyncio
async def test_structured_call_retries_on_bad_json(prompts_dir, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """First call returns prose; second call returns valid JSON."""
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(
        side_effect=[
            _fake_response("Sure, here's the answer."),
            _fake_response('{"name": "beta", "count": 3}'),
        ]
    )
    client = ClaudeClient()
    monkeypatch.setattr(client, "_ensure_client", lambda: fake_client)

    result = await client.structured(
        prompt_file="tiny.md",
        context={"name": "x", "count": 1},
        schema=TinyOut,
        model=LIBRARIAN_MODEL,
        agent="librarian",
    )
    assert result.parsed.count == 3
    assert fake_client.messages.create.call_count == 2


@pytest.mark.asyncio
async def test_structured_call_strips_code_fences(prompts_dir, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Model wraps JSON in ```json fences — client recovers."""
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(
        return_value=_fake_response('```json\n{"name": "gamma", "count": 9}\n```')
    )
    client = ClaudeClient()
    monkeypatch.setattr(client, "_ensure_client", lambda: fake_client)

    result = await client.structured(
        prompt_file="tiny.md",
        context={"name": "x", "count": 1},
        schema=TinyOut,
        model=LIBRARIAN_MODEL,
        agent="librarian",
    )
    assert result.parsed.name == "gamma"


@pytest.mark.asyncio
async def test_structured_call_raises_after_exhausted_retries(
    prompts_dir, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """All attempts return bad JSON → StructuredOutputError + failure row in api_usage."""
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(
        return_value=_fake_response("not json — sorry")
    )
    client = ClaudeClient()
    monkeypatch.setattr(client, "_ensure_client", lambda: fake_client)

    with pytest.raises(StructuredOutputError) as exc_info:
        await client.structured(
            prompt_file="tiny.md",
            context={"name": "x", "count": 1},
            schema=TinyOut,
            model=LIBRARIAN_MODEL,
            agent="librarian",
        )
    assert exc_info.value.raw_output  # raw preserved for inspection
    assert "json" in exc_info.value.last_error

    from sqlmodel import Session, select

    with Session(get_engine()) as session:
        rows = session.exec(select(ApiUsage)).all()
    assert any(r.succeeded is False for r in rows)


@pytest.mark.asyncio
async def test_schema_validation_failure_retries(prompts_dir, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """JSON parses but doesn't match schema — client retries with stricter prompt."""
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(
        side_effect=[
            _fake_response('{"wrong_field": "oops"}'),
            _fake_response('{"name": "delta", "count": 1}'),
        ]
    )
    client = ClaudeClient()
    monkeypatch.setattr(client, "_ensure_client", lambda: fake_client)

    result = await client.structured(
        prompt_file="tiny.md",
        context={"name": "x", "count": 1},
        schema=TinyOut,
        model=LIBRARIAN_MODEL,
        agent="librarian",
    )
    assert result.parsed.name == "delta"
