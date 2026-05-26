"""Anthropic Claude client wrapper — the single funnel for every Claude API call.

Per `CLAUDE.md` §"Handling Claude API Calls":
    - Retries (3 attempts, exponential backoff) on transient failures
    - Logs token usage + cost to `api_usage` table per call
    - Validates structured outputs against a Pydantic schema before returning
    - Never exposes the raw API key to calling code

Per `feedback-network-timeouts` memory:
    - Explicit long timeouts at construction; never SDK defaults.

Per `feedback-claude-first` memory + `model-tiers` memory:
    - The `model` kwarg is REQUIRED on every call; the client never picks a default.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from anthropic import AsyncAnthropic
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from loguru import logger
from pydantic import BaseModel, ValidationError
from sqlmodel import Session

from synapse.config import (
    ANTHROPIC_CONNECT_TIMEOUT_SECONDS,
    ANTHROPIC_MAX_TOKENS,
    ANTHROPIC_READ_TIMEOUT_SECONDS,
    HTTP_BACKOFF_FACTOR,
    HTTP_INITIAL_BACKOFF_SECONDS,
    HTTP_MAX_RETRIES,
    MODEL_PRICING_USD_PER_MTOK,
    MODELS_WITHOUT_TEMPERATURE,
    get_settings,
)
from synapse.graph.db import get_engine
from synapse.graph.models import ApiUsage

T = TypeVar("T", bound=BaseModel)

# JSON parse + retry instruction
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


class LLMError(Exception):
    """Base for client errors."""


class StructuredOutputError(LLMError):
    """Raised when an LLM response cannot be coerced into the requested schema."""

    def __init__(self, message: str, raw_output: str, last_error: str) -> None:
        super().__init__(message)
        self.raw_output = raw_output
        self.last_error = last_error


@dataclass
class CallResult:
    """Bundled return from a Claude call: parsed payload + usage + raw text."""

    parsed: BaseModel
    raw: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    latency_ms: int
    cost_usd: float


# ── Prompt template loading ──────────────────────────────────────────────────


def _prompt_env() -> Environment:
    """Jinja2 env scoped to the packaged `synapse/prompts/` directory."""
    return Environment(
        loader=FileSystemLoader(get_settings().prompts_dir),
        undefined=StrictUndefined,        # missing variables raise instead of silent ''
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,
        keep_trailing_newline=True,
    )


def render_prompt(prompt_file: str, context: dict[str, Any]) -> str:
    """Render a `.md` prompt template with the given context.

    Args:
        prompt_file: Filename (e.g., "librarian.md") inside `synapse/prompts/`.
        context: Variables for Jinja2 interpolation. Missing keys raise.

    Returns:
        Fully rendered prompt body, ready to send as a Claude user message.
    """
    env = _prompt_env()
    template = env.get_template(prompt_file)
    return template.render(**context)


# ── JSON extraction ──────────────────────────────────────────────────────────


def _strip_code_fence(text: str) -> str:
    """If the response is wrapped in ```json … ```, return the inner block."""
    m = _JSON_BLOCK_RE.search(text)
    return m.group(1) if m else text


def _parse_json(raw: str) -> Any:
    """Try strict JSON first; fall back to fenced-block extraction."""
    candidate = raw.strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    stripped = _strip_code_fence(candidate).strip()
    return json.loads(stripped)  # raises JSONDecodeError if still bad


# ── Cost computation ─────────────────────────────────────────────────────────


def _cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate the call cost in USD using `MODEL_PRICING_USD_PER_MTOK`."""
    pricing = MODEL_PRICING_USD_PER_MTOK.get(model)
    if pricing is None:
        return 0.0
    in_price, out_price = pricing
    return (input_tokens * in_price + output_tokens * out_price) / 1_000_000.0


# ── Usage logging ────────────────────────────────────────────────────────────


def _log_usage(
    *,
    agent: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read: int,
    cache_creation: int,
    cost_usd: float,
    latency_ms: int,
    succeeded: bool,
    error: str = "",
) -> None:
    """Persist a row to `api_usage`. Best-effort; never raises."""
    row = ApiUsage(
        id=str(uuid.uuid4()),
        agent=agent,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        succeeded=succeeded,
        error=error,
    )
    try:
        with Session(get_engine()) as session:
            session.add(row)
            session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("api_usage insert failed: {exc}", exc=exc)


# ── Client ───────────────────────────────────────────────────────────────────


class ClaudeClient:
    """Async Anthropic client with structured-output validation + audit logging."""

    def __init__(self) -> None:
        self._client: AsyncAnthropic | None = None

    def _ensure_client(self) -> AsyncAnthropic:
        """Lazy-construct so importing this module doesn't require a key."""
        if self._client is None:
            settings = get_settings()
            if not settings.anthropic_api_key:
                raise LLMError(
                    "ANTHROPIC_API_KEY is not set. Add it to .env before running agents."
                )
            self._client = AsyncAnthropic(
                api_key=settings.anthropic_api_key,
                timeout=ANTHROPIC_READ_TIMEOUT_SECONDS,
                max_retries=0,  # we own retry policy
            )
        return self._client

    async def _call_once(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        system: str | None,
    ) -> tuple[str, Any, int]:
        """Single Anthropic call. Returns (raw_text, usage, latency_ms)."""
        client = self._ensure_client()
        start = time.perf_counter()
        message_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        # Some reasoning models (Opus 4.7+) reject the temperature param.
        if model not in MODELS_WITHOUT_TEMPERATURE:
            message_kwargs["temperature"] = temperature
        if system is not None:
            message_kwargs["system"] = system

        response = await client.messages.create(**message_kwargs)
        latency_ms = int((time.perf_counter() - start) * 1000)

        # Anthropic returns a list of content blocks; we expect text-only.
        text_parts: list[str] = []
        for block in response.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(block.text)
        raw_text = "".join(text_parts)
        return raw_text, response.usage, latency_ms

    async def structured(
        self,
        *,
        prompt_file: str,
        context: dict[str, Any],
        schema: type[T],
        model: str,
        agent: str,
        temperature: float = 0.3,
        max_tokens: int = ANTHROPIC_MAX_TOKENS,
        system: str | None = None,
    ) -> CallResult:
        """Call Claude with a templated prompt and validate the response.

        Args:
            prompt_file: Filename under `synapse/prompts/` (e.g. "librarian.md").
            context: Jinja2 variables for the prompt.
            schema: Pydantic model the response must validate against.
            model: Required — pick from `synapse.config.{LIBRARIAN,SYNTHESIZER,...}_MODEL`.
            agent: Logical agent name for the api_usage audit trail.
            temperature: Sampling temperature (default 0.3 — bias toward deterministic).
            max_tokens: Output cap.
            system: Optional system prompt.

        Returns:
            CallResult with `parsed` populated by the validated Pydantic instance.

        Raises:
            StructuredOutputError: If after `HTTP_MAX_RETRIES` the response still
                doesn't parse + validate. The raw output is preserved on the exception.
            LLMError: If ANTHROPIC_API_KEY is missing.
        """
        prompt = render_prompt(prompt_file, context)
        last_error = ""
        last_raw = ""
        total_in = total_out = total_cache_r = total_cache_c = 0
        total_latency = 0

        for attempt in range(HTTP_MAX_RETRIES):
            attempt_prompt = prompt if attempt == 0 else self._retry_prompt(prompt, last_error)
            try:
                raw_text, usage, latency_ms = await self._call_once(
                    model=model,
                    prompt=attempt_prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system,
                )
            except Exception as exc:  # noqa: BLE001 — network/transport errors
                last_error = f"transport: {exc}"
                last_raw = ""
                logger.warning(
                    "claude call attempt {n}/{max} failed: {exc}",
                    n=attempt + 1,
                    max=HTTP_MAX_RETRIES,
                    exc=exc,
                )
                await asyncio.sleep(
                    HTTP_INITIAL_BACKOFF_SECONDS * (HTTP_BACKOFF_FACTOR ** attempt)
                )
                continue

            total_in += getattr(usage, "input_tokens", 0)
            total_out += getattr(usage, "output_tokens", 0)
            total_cache_r += getattr(usage, "cache_read_input_tokens", 0) or 0
            total_cache_c += getattr(usage, "cache_creation_input_tokens", 0) or 0
            total_latency += latency_ms
            last_raw = raw_text

            # Parse JSON
            try:
                payload = _parse_json(raw_text)
            except json.JSONDecodeError as exc:
                last_error = f"json: {exc}"
                logger.warning(
                    "claude returned non-JSON (attempt {n}): {snippet}",
                    n=attempt + 1,
                    snippet=raw_text[:200],
                )
                continue

            # Validate schema
            try:
                parsed = schema.model_validate(payload)
            except ValidationError as exc:
                last_error = f"schema: {exc}"
                logger.warning(
                    "claude payload failed schema validation (attempt {n}): {err}",
                    n=attempt + 1,
                    err=str(exc)[:300],
                )
                continue

            # Success.
            cost = _cost_usd(model, total_in, total_out)
            _log_usage(
                agent=agent,
                model=model,
                input_tokens=total_in,
                output_tokens=total_out,
                cache_read=total_cache_r,
                cache_creation=total_cache_c,
                cost_usd=cost,
                latency_ms=total_latency,
                succeeded=True,
            )
            return CallResult(
                parsed=parsed,
                raw=raw_text,
                input_tokens=total_in,
                output_tokens=total_out,
                cache_read_tokens=total_cache_r,
                cache_creation_tokens=total_cache_c,
                latency_ms=total_latency,
                cost_usd=cost,
            )

        # Exhausted retries.
        cost = _cost_usd(model, total_in, total_out)
        _log_usage(
            agent=agent,
            model=model,
            input_tokens=total_in,
            output_tokens=total_out,
            cache_read=total_cache_r,
            cache_creation=total_cache_c,
            cost_usd=cost,
            latency_ms=total_latency,
            succeeded=False,
            error=last_error,
        )
        logger.error(
            "claude structured call exhausted retries; last_error={err}; raw_snippet={snippet}",
            err=last_error,
            snippet=last_raw[:500],
        )
        raise StructuredOutputError(
            f"structured call to {model} for agent={agent} failed after "
            f"{HTTP_MAX_RETRIES} attempts",
            raw_output=last_raw,
            last_error=last_error,
        )

    @staticmethod
    def _retry_prompt(original: str, last_error: str) -> str:
        """Append a stricter formatting instruction for retries."""
        return (
            original
            + "\n\n---\n"
            + "Your previous response was rejected. Reason: "
            + last_error
            + "\n\nReturn ONLY valid JSON matching the schema. "
            + "No preamble. No markdown code fences. No explanation. "
            + "Begin your response with `{`."
        )

    async def aclose(self) -> None:
        """Close the underlying httpx client. Idempotent."""
        if self._client is not None:
            await self._client.close()
            self._client = None


# Module-level singleton, lazy-initialized on first use.
claude = ClaudeClient()
