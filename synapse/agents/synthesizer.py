"""The Synthesizer — daily Delta Briefing generator (PRD §7.2).

Runs scheduled at 07:00 Africa/Nairobi via APScheduler, plus on demand via
`synapse brief` or `POST /agents/synthesizer/run`. Each run:

    1. Collects: due CONCEPTs, horizon EVENTs, stale open QUESTIONs,
       session state, recent INSIGHT candidates (read-only from pending file).
    2. Calls Claude Opus 4.7 (`SYNTHESIZER_MODEL`) via the LLM client.
    3. Validates the JSON against `SynthesizerOutput`.
    4. Renders the brief as a markdown artifact and writes to
       `${VAULT}/daily/YYYY-MM-DD.md`.
    5. Pushes each generated application question into its concept's rotating
       question bank (so the bank stays fresh — PRD §7.2).
    6. (If a Telegram chat id is configured) sends the rendered brief to Telegram.

The Synthesizer NEVER writes graph nodes. It surfaces; the user decides.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from loguru import logger
from pydantic import BaseModel, Field
from sqlmodel import Session, select
from zoneinfo import ZoneInfo

from synapse.agents.base import Agent, AgentResult
from synapse.config import (
    LIBRARIAN_PENDING_INSIGHTS_FILE,
    SYNAPSE_TIMEZONE,
    SYNTHESIZER_DAILY_FILE_FORMAT,
    SYNTHESIZER_MODEL,
    SYNTHESIZER_OPEN_QUESTION_AGE_DAYS,
    SYNTHESIZER_RETENTION_ALERTS,
    get_settings,
)
from synapse.context.horizon import refresh_horizon
from synapse.context.session import get_session
from synapse.graph.db import get_engine
from synapse.graph.models import Node, NodeType
from synapse.graph.retention import get_due_reviews, push_question
from synapse.llm.client import ClaudeClient, StructuredOutputError, claude


# ── Pydantic output schema ───────────────────────────────────────────────────


class _RetentionAlert(BaseModel):
    node_id: str
    title: str
    application_question: str
    why_now: str = ""


class _HorizonPrep(BaseModel):
    event_node_id: str
    event_title: str
    hours_until: int
    prep_summary: str
    prep_concept_titles: list[str] = Field(default_factory=list)


class _Bridge(BaseModel):
    headline: str
    academic_anchor: str
    startup_anchor: str
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)


class _OpenQuestion(BaseModel):
    node_id: str
    title: str
    prompt: str


class SynthesizerOutput(BaseModel):
    """The exact schema the Synthesizer prompt is contracted to return."""

    confidence: float = Field(ge=0.0, le=1.0)
    retention_alerts: list[_RetentionAlert] = Field(default_factory=list)
    horizon_prep: list[_HorizonPrep] = Field(default_factory=list)
    bridge: _Bridge | None = None
    open_question: _OpenQuestion | None = None
    summary_line: str = ""


# ── Context collection ──────────────────────────────────────────────────────


@dataclass
class _SynthesizerContext:
    """Everything the prompt template needs."""

    today_iso: str
    foreground_task: str
    energy_estimate: str
    retention_candidates: list[dict[str, Any]]
    horizon_items: list[dict[str, Any]]
    open_questions: list[dict[str, Any]]
    insight_candidates: list[str]


def _collect_context() -> _SynthesizerContext:
    settings = get_settings()
    tz = ZoneInfo(SYNAPSE_TIMEZONE)
    now_local = datetime.now(tz=tz)

    snap = get_session()

    # Retention candidates.
    due = get_due_reviews(limit=SYNTHESIZER_RETENTION_ALERTS)
    retention_candidates = [
        {
            "node_id": d.node_id,
            "title": d.title,
            "content": d.content,
            "review_count": d.review_count,
            "ease_factor": round(d.ease_factor, 2),
            "application_question": d.application_question,
        }
        for d in due
    ]

    # Horizon items — refresh first so any new EVENT shows up + pre-loading fires.
    refresh_horizon()
    snap = get_session()  # re-read after refresh
    now_utc = datetime.now(tz=timezone.utc)
    horizon_items: list[dict[str, Any]] = []
    for h in snap.horizon:
        try:
            when = datetime.fromisoformat(h.date)
        except ValueError:
            continue
        hours_until = max(0, int((when - now_utc).total_seconds() // 3600))
        # Look up linked concept titles by id.
        titles: list[str] = []
        with Session(get_engine()) as db:
            for cid in h.prep_concept_ids:
                row = db.get(Node, cid)
                if row is not None:
                    titles.append(row.title)
        horizon_items.append(
            {
                "event_node_id": h.event_node_id,
                "title": h.title,
                "hours_until": hours_until,
                "linked_concept_titles": titles,
            }
        )

    # Open QUESTIONs older than threshold. QUESTION nodes use `tags` for status
    # encoding for now (no extra column); a status="open" entry is the default.
    open_questions: list[dict[str, Any]] = []
    cutoff = now_utc - timedelta(days=SYNTHESIZER_OPEN_QUESTION_AGE_DAYS)
    with Session(get_engine()) as db:
        rows = list(db.exec(select(Node).where(Node.type == NodeType.QUESTION)).all())
    for q in rows:
        if q.created_at and q.created_at > cutoff:
            continue  # too fresh
        try:
            tags = json.loads(q.tags or "[]")
        except json.JSONDecodeError:
            tags = []
        if any(isinstance(t, str) and t.startswith("status=") and t != "status=open" for t in tags):
            continue  # explicitly not-open
        open_for_days = (
            (now_utc - q.created_at).days if q.created_at else SYNTHESIZER_OPEN_QUESTION_AGE_DAYS
        )
        open_questions.append(
            {
                "node_id": q.id,
                "title": q.title,
                "content": q.content,
                "open_for_days": open_for_days,
            }
        )

    # Insight candidates — read most-recent N blocks from pending_insights.md.
    insight_path = settings.synapse_vault_path / LIBRARIAN_PENDING_INSIGHTS_FILE
    insight_candidates: list[str] = []
    if insight_path.exists():
        try:
            text = insight_path.read_text(encoding="utf-8")
            # Each "## From ..." block is one batch; lines starting with "- " are insights.
            for line in text.splitlines():
                if line.startswith("- "):
                    insight_candidates.append(line[2:].strip())
        except OSError as exc:
            logger.warning("could not read pending_insights.md: {exc}", exc=exc)
    # Cap to most-recent 10 to avoid bloating the prompt.
    insight_candidates = insight_candidates[-10:]

    return _SynthesizerContext(
        today_iso=now_local.date().isoformat(),
        foreground_task=snap.foreground.task,
        energy_estimate=snap.energy_estimate,
        retention_candidates=retention_candidates,
        horizon_items=horizon_items,
        open_questions=open_questions,
        insight_candidates=insight_candidates,
    )


# ── Rendering ────────────────────────────────────────────────────────────────


def render_delta_briefing(payload: SynthesizerOutput, *, date_iso: str) -> str:
    """Convert a SynthesizerOutput into the PRD §7.2 markdown brief."""
    out: list[str] = [f"## Delta Briefing — {date_iso}", ""]

    out.append("### 🔴 Retention Alerts")
    if payload.retention_alerts:
        for r in payload.retention_alerts:
            out.append(f"- **{r.title}**")
            out.append(f"  - _{r.why_now}_" if r.why_now else "")
            out.append(f"  - {r.application_question}")
    else:
        out.append("_no overdue reviews today_")
    out.append("")

    out.append("### 📅 Horizon Prep")
    if payload.horizon_prep:
        for h in payload.horizon_prep:
            out.append(f"- **{h.event_title}** — in {h.hours_until}h")
            out.append(f"  - {h.prep_summary}")
            if h.prep_concept_titles:
                out.append(f"  - Concepts: {', '.join(h.prep_concept_titles)}")
    else:
        out.append("_no events in the next 72h_")
    out.append("")

    out.append("### ⚡ Bridge")
    if payload.bridge is not None:
        b = payload.bridge
        out.append(f"**{b.headline}**")
        out.append("")
        out.append(f"- Academic anchor: {b.academic_anchor}")
        out.append(f"- Startup anchor: {b.startup_anchor}")
        out.append("")
        out.append(b.reasoning)
    else:
        out.append("_no high-confidence bridge today_")
    out.append("")

    out.append("### ❓ Open Question")
    if payload.open_question is not None:
        q = payload.open_question
        out.append(f"- **{q.title}**")
        out.append(f"  - {q.prompt}")
    else:
        out.append("_no stale open questions_")
    out.append("")

    if payload.summary_line:
        out.append("---")
        out.append(f"_{payload.summary_line}_")
    return "\n".join(line for line in out if line is not None) + "\n"


def _daily_brief_path(date_iso: str) -> Path:
    settings = get_settings()
    daily_dir = settings.synapse_vault_path / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    name = datetime.fromisoformat(date_iso).strftime(SYNTHESIZER_DAILY_FILE_FORMAT)
    return daily_dir / name


# ── Agent ────────────────────────────────────────────────────────────────────


class Synthesizer(Agent):
    """Generates the Delta Briefing. `name = synthesizer`."""

    name = "synthesizer"

    def __init__(self, *, client: ClaudeClient | None = None) -> None:
        self._client = client or claude

    async def run(self) -> AgentResult:  # type: ignore[override]
        """Generate one Delta Briefing artifact."""
        ctx = _collect_context()
        prompt_context = {
            "today_iso": ctx.today_iso,
            "foreground_task": ctx.foreground_task,
            "energy_estimate": ctx.energy_estimate,
            "retention_candidates": ctx.retention_candidates,
            "horizon_items": ctx.horizon_items,
            "open_questions": ctx.open_questions,
            "insight_candidates": ctx.insight_candidates,
            "retention_alerts_max": SYNTHESIZER_RETENTION_ALERTS,
            "open_question_age_days": SYNTHESIZER_OPEN_QUESTION_AGE_DAYS,
        }

        try:
            result = await self._client.structured(
                prompt_file="synthesizer.md",
                context=prompt_context,
                schema=SynthesizerOutput,
                model=SYNTHESIZER_MODEL,
                agent=self.name,
                temperature=0.4,
                max_tokens=4096,
            )
        except StructuredOutputError as exc:
            return AgentResult(
                agent=self.name,
                ok=False,
                summary=f"synthesizer failed: {exc.last_error}",
                errors=[str(exc)],
            )

        payload: SynthesizerOutput = result.parsed  # type: ignore[assignment]

        # Push each generated application question into its concept's rotating bank.
        questions_pushed = 0
        for alert in payload.retention_alerts:
            n = push_question(alert.node_id, alert.application_question)
            if n > 0:
                questions_pushed += 1

        # Write the daily brief markdown.
        brief_md = render_delta_briefing(payload, date_iso=ctx.today_iso)
        out_path = _daily_brief_path(ctx.today_iso)
        out_path.write_text(brief_md, encoding="utf-8")

        return AgentResult(
            agent=self.name,
            ok=True,
            summary=(
                payload.summary_line
                or f"brief written to {out_path.name}"
            ),
            artifacts={
                "daily_path": out_path.as_posix(),
                "brief_markdown": brief_md,
                "retention_alerts": len(payload.retention_alerts),
                "horizon_items": len(payload.horizon_prep),
                "bridge": payload.bridge is not None,
                "open_question": payload.open_question is not None,
                "questions_pushed": questions_pushed,
                "confidence": payload.confidence,
                "cost_usd": result.cost_usd,
            },
        )


synthesizer = Synthesizer()
