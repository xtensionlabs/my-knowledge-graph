"""The Guardian — burnout watchdog. PRD §6.6 / §7.6 / Appendix A.6.

Runs every 4 hours via APScheduler. Reads three signals:

    1. Capture quality trend — falling avg capture size + rate over the
       configured window.
    2. Retention lapse count — how many CONCEPT next_reviews are overdue.
    3. Last nudge timestamp — honors a cooldown so the Guardian doesn't
       become noisy.

Output is at most ONE ≤2-line nudge in `vault/daily/guardian_nudges.md` or
silence. Silence is the preferred outcome.

Model: Haiku 4.5 (`GUARDIAN_MODEL` per `model-tiers` memory) — cheap, fast,
matches the "fire fast or stay quiet" job profile.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field
from sqlmodel import Session, func, select

from synapse.agents.base import Agent, AgentResult
from synapse.config import (
    GUARDIAN_CAPTURE_QUALITY_MIN_AVG_BYTES,
    GUARDIAN_CAPTURE_QUALITY_WINDOW_HOURS,
    GUARDIAN_MODEL,
    GUARDIAN_NUDGE_COOLDOWN_HOURS,
    GUARDIAN_NUDGE_MAX_LINES,
    GUARDIAN_RETENTION_LAPSE_THRESHOLD,
    get_settings,
)
from synapse.context.energy import current_energy
from synapse.graph.db import get_engine
from synapse.graph.models import CaptureLog, Node, NodeType
from synapse.llm.client import ClaudeClient, StructuredOutputError, claude
from synapse.utils.time import assume_utc as _assume_utc, utcnow as _utcnow

NUDGES_FILENAME = "guardian_nudges.md"


class GuardianOutput(BaseModel):
    """Strict schema for the Guardian's JSON return."""

    nudge: bool
    reason: str = ""
    message: str = ""
    scope_suggestion: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


@dataclass
class GuardianSignals:
    """Snapshot of the inputs the Guardian saw, for tests + logging."""

    capture_count: int
    avg_size_bytes: float
    overdue_count: int
    avg_lapse_hours: float
    hours_since_last_nudge: float
    strategist_artifact_ignored: bool
    energy: str
    threshold_fired: list[str] = field(default_factory=list)


def _hours_since_last_nudge(nudges_path: Path) -> float:
    """Return hours since the last entry in guardian_nudges.md (∞ if none)."""
    if not nudges_path.is_file():
        return 1e9
    mtime = datetime.fromtimestamp(nudges_path.stat().st_mtime, tz=timezone.utc)
    return (_utcnow() - mtime).total_seconds() / 3600.0


def gather_signals() -> GuardianSignals:
    """Compute the Guardian's inputs. Pure read; never mutates."""
    now = _utcnow()
    window_start = now - timedelta(hours=GUARDIAN_CAPTURE_QUALITY_WINDOW_HOURS)

    with Session(get_engine()) as session:
        # Capture count + avg size
        cap_rows = list(
            session.exec(
                select(CaptureLog).where(CaptureLog.created_at >= window_start)
            ).all()
        )
        capture_count = len(cap_rows)
        avg_size = (sum(r.size_bytes for r in cap_rows) / capture_count) if capture_count else 0.0

        # Overdue concept reviews
        concepts = list(
            session.exec(select(Node).where(Node.type == NodeType.CONCEPT)).all()
        )
        overdue: list[Node] = []
        for c in concepts:
            nr = _assume_utc(c.next_review)
            if nr is not None and nr < now:
                overdue.append(c)
        if overdue:
            lapses_h = [(now - _assume_utc(c.next_review)).total_seconds() / 3600.0 for c in overdue if _assume_utc(c.next_review)]
            avg_lapse = sum(lapses_h) / len(lapses_h) if lapses_h else 0.0
        else:
            avg_lapse = 0.0

    settings = get_settings()
    nudges_path = settings.synapse_vault_path / "daily" / NUDGES_FILENAME
    hours_since = _hours_since_last_nudge(nudges_path)

    # Strategist artifact "ignored" heuristic: a strategy file from the last 48h
    # exists, but nothing in its directory has been touched since.
    strategist_ignored = False
    strat_dir = settings.synapse_vault_path / "strategy"
    if strat_dir.is_dir():
        cutoff = now - timedelta(hours=48)
        for f in strat_dir.glob("*.md"):
            try:
                created = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if created >= cutoff:
                strategist_ignored = True  # exists; engagement signal not tracked yet
                break

    # Determine which thresholds fired
    fired: list[str] = []
    if avg_size < GUARDIAN_CAPTURE_QUALITY_MIN_AVG_BYTES and capture_count >= 3:
        fired.append("capture_quality")
    if len(overdue) >= GUARDIAN_RETENTION_LAPSE_THRESHOLD:
        fired.append("retention_lapse")

    return GuardianSignals(
        capture_count=capture_count,
        avg_size_bytes=avg_size,
        overdue_count=len(overdue),
        avg_lapse_hours=avg_lapse,
        hours_since_last_nudge=hours_since,
        strategist_artifact_ignored=strategist_ignored,
        energy=current_energy(),
        threshold_fired=fired,
    )


def _enforce_line_cap(text: str, max_lines: int = GUARDIAN_NUDGE_MAX_LINES) -> str:
    """Hard cap on output lines. Truncate with ellipsis if exceeded."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) <= max_lines:
        return "\n".join(lines)
    capped = lines[:max_lines]
    capped[-1] = capped[-1].rstrip() + " …"
    return "\n".join(capped)


def _append_nudge(nudges_path: Path, message: str, reason: str) -> None:
    """Append a nudge entry to guardian_nudges.md (atomic-ish; small file)."""
    nudges_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = _utcnow().isoformat()
    block = f"\n## {stamp} — {reason}\n\n{message}\n"
    with nudges_path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(block)


class Guardian(Agent):
    """The Guardian — `name = guardian`."""

    name = "guardian"

    def __init__(self, *, client: ClaudeClient | None = None) -> None:
        self._client = client or claude

    async def run(self) -> AgentResult:  # type: ignore[override]
        signals = gather_signals()

        # Short-circuit: if cooldown is still in effect, never call the LLM.
        if signals.hours_since_last_nudge < GUARDIAN_NUDGE_COOLDOWN_HOURS:
            return AgentResult(
                agent=self.name,
                ok=True,
                summary=f"silent (cooldown: {signals.hours_since_last_nudge:.1f}h ago)",
                artifacts={"nudge": False, "reason": "cooldown"},
            )

        # Short-circuit: if no threshold fired AND nothing else triggered, stay silent
        # without burning tokens.
        if not signals.threshold_fired and not signals.strategist_artifact_ignored:
            return AgentResult(
                agent=self.name,
                ok=True,
                summary="silent (no thresholds fired)",
                artifacts={"nudge": False, "reason": "below threshold"},
            )

        # Otherwise, ask Haiku to decide tone + phrasing.
        prompt_context = {
            "window_hours": GUARDIAN_CAPTURE_QUALITY_WINDOW_HOURS,
            "capture_count": signals.capture_count,
            "avg_size_bytes": round(signals.avg_size_bytes, 1),
            "overdue_count": signals.overdue_count,
            "avg_lapse_hours": round(signals.avg_lapse_hours, 1),
            "hours_since_last_nudge": round(signals.hours_since_last_nudge, 1),
            "strategist_artifact_ignored": signals.strategist_artifact_ignored,
            "energy": signals.energy,
            "min_avg_bytes": GUARDIAN_CAPTURE_QUALITY_MIN_AVG_BYTES,
            "retention_threshold": GUARDIAN_RETENTION_LAPSE_THRESHOLD,
            "cooldown_hours": GUARDIAN_NUDGE_COOLDOWN_HOURS,
        }
        try:
            result = await self._client.structured(
                prompt_file="guardian.md",
                context=prompt_context,
                schema=GuardianOutput,
                model=GUARDIAN_MODEL,
                agent=self.name,
                temperature=0.2,
                max_tokens=512,
            )
        except StructuredOutputError as exc:
            return AgentResult(
                agent=self.name,
                ok=False,
                summary=f"guardian LLM call failed: {exc.last_error}",
                errors=[exc.last_error],
            )

        output: GuardianOutput = result.parsed  # type: ignore[assignment]
        if not output.nudge:
            return AgentResult(
                agent=self.name,
                ok=True,
                summary=f"silent ({output.reason or 'no nudge'})",
                artifacts={"nudge": False, "reason": output.reason},
            )

        message = _enforce_line_cap(output.message)
        if not message:
            return AgentResult(
                agent=self.name,
                ok=True,
                summary="silent (empty message after cap)",
                artifacts={"nudge": False, "reason": "empty"},
            )

        settings = get_settings()
        nudges_path = settings.synapse_vault_path / "daily" / NUDGES_FILENAME
        _append_nudge(nudges_path, message, output.reason or "unspecified")

        logger.info(
            "guardian nudge: reason={r}, lines={n}",
            r=output.reason,
            n=len(message.splitlines()),
        )

        return AgentResult(
            agent=self.name,
            ok=True,
            summary=f"nudged: {output.reason}",
            artifacts={
                "nudge": True,
                "reason": output.reason,
                "message": message,
                "scope_suggestion": output.scope_suggestion,
                "nudges_path": str(nudges_path),
            },
        )


guardian = Guardian()
