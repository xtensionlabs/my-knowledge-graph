"""The Critic — manual-trigger adversarial reader.

Critiques any text artifact (briefing, strategy report, draft, code) and
returns exactly one "most important fix" — or honest praise if the output
is genuinely good. The one-fix discipline is the product.

Model: Opus 4.7 (`CRITIC_MODEL`) — the whole point is sharper-than-the-user
judgment; the strongest model is required.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, Field

from synapse.agents.base import Agent, AgentResult
from synapse.config import (
    ANTHROPIC_MAX_TOKENS,
    CRITIC_MAX_ARTIFACT_BYTES,
    CRITIC_MODEL,
)
from synapse.llm.client import ClaudeClient, StructuredOutputError, claude


class CriticOutput(BaseModel):
    """Strict schema for the Critic's JSON return."""

    confidence: float = Field(ge=0.0, le=1.0)
    is_good: bool = False
    headline: str
    diagnosis: str = ""
    concrete_change: str = ""
    what_else_you_considered: str = ""


@dataclass
class CritiqueRequest:
    """Bundled inputs to the Critic."""

    artifact: str
    artifact_kind: str = "freeform"
    context: str = ""


class Critic(Agent):
    """The Critic agent — `name = critic`."""

    name = "critic"

    def __init__(self, *, client: ClaudeClient | None = None) -> None:
        self._client = client or claude

    async def run(self, *, request: CritiqueRequest) -> AgentResult:  # type: ignore[override]
        """Critique the request's artifact.

        Args:
            request: Bundled artifact + kind + context.

        Returns:
            AgentResult with the Critic's CriticOutput in `artifacts['payload']`.
        """
        artifact = request.artifact
        if len(artifact.encode("utf-8")) > CRITIC_MAX_ARTIFACT_BYTES:
            return AgentResult(
                agent=self.name,
                ok=False,
                summary=f"artifact too large ({len(artifact)} bytes > {CRITIC_MAX_ARTIFACT_BYTES})",
                errors=["artifact_too_large"],
            )
        if not artifact.strip():
            return AgentResult(
                agent=self.name,
                ok=False,
                summary="empty artifact",
                errors=["empty_artifact"],
            )

        prompt_context = {
            "artifact": artifact,
            "artifact_kind": request.artifact_kind,
            "context": request.context,
        }
        try:
            result = await self._client.structured(
                prompt_file="critic.md",
                context=prompt_context,
                schema=CriticOutput,
                model=CRITIC_MODEL,
                agent=self.name,
                temperature=0.3,
                max_tokens=ANTHROPIC_MAX_TOKENS,
            )
        except StructuredOutputError as exc:
            return AgentResult(
                agent=self.name,
                ok=False,
                summary=f"critic call failed: {exc.last_error}",
                errors=[exc.last_error],
            )

        output: CriticOutput = result.parsed  # type: ignore[assignment]
        verdict = "GOOD" if output.is_good else "FIX"
        logger.info(
            "critic: {v} ({k}) — {h}",
            v=verdict, k=request.artifact_kind, h=output.headline[:80],
        )
        return AgentResult(
            agent=self.name,
            ok=True,
            summary=f"[{verdict}] {output.headline}",
            artifacts={
                "verdict": verdict,
                "headline": output.headline,
                "diagnosis": output.diagnosis,
                "concrete_change": output.concrete_change,
                "what_else_you_considered": output.what_else_you_considered,
                "confidence": output.confidence,
                "cost_usd": result.cost_usd,
            },
        )


def critique_file(path: Path, kind: str = "freeform", context: str = "") -> CritiqueRequest:
    """Convenience constructor: build a CritiqueRequest from a file path."""
    text = path.read_text(encoding="utf-8")
    return CritiqueRequest(artifact=text, artifact_kind=kind, context=context)


critic = Critic()
