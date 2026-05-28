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

    # ── Sleep consolidation pass (PRD Appendix A.2) ──────────────────────────

    async def consolidate(self) -> AgentResult:
        """Nightly consolidation — surface abstractions across fresh nodes.

        Runs at `CONSOLIDATION_HOUR` (default 02:00 local). Reads CONCEPT and
        FACT nodes touched in the last `CONSOLIDATION_LOOKBACK_HOURS` and asks
        Claude to propose generalizable principles connecting ≥2 of them.

        Output: zero-or-more INSIGHT candidates appended to
        `pending_insights.md`. Never auto-creates INSIGHT nodes (user must
        confirm via `synapse insight confirm`).
        """
        from synapse.agents.librarian import _append_pending, _format_insight, _InsightCandidate
        from synapse.config import (
            CONSOLIDATION_LOOKBACK_HOURS,
            CONSOLIDATION_MIN_NODES,
        )
        from synapse.graph.hebbian import strengthen_edges

        # ── 1. Gather fresh CONCEPT and FACT nodes ────────────────────────────
        now = datetime.now(tz=timezone.utc)
        cutoff = now - timedelta(hours=CONSOLIDATION_LOOKBACK_HOURS)
        fresh_nodes: list[dict[str, Any]] = []
        fresh_ids: list[str] = []
        with Session(get_engine()) as session:
            for node_type in (NodeType.CONCEPT, NodeType.FACT):
                rows = session.exec(
                    select(Node).where(Node.type == node_type)
                ).all()
                for n in rows:
                    updated = n.updated_at
                    if updated is None:
                        continue
                    if updated.tzinfo is None:
                        updated = updated.replace(tzinfo=timezone.utc)
                    if updated < cutoff:
                        continue
                    excerpt = (n.content or "").split("\n\n")[0][:200]
                    fresh_nodes.append(
                        {
                            "type": n.type.value if hasattr(n.type, "value") else str(n.type),
                            "title": n.title,
                            "content_excerpt": excerpt,
                        }
                    )
                    fresh_ids.append(n.id)

            if len(fresh_nodes) < CONSOLIDATION_MIN_NODES:
                return AgentResult(
                    agent="synthesizer-consolidate",
                    ok=True,
                    summary=f"skipped — only {len(fresh_nodes)} fresh nodes (need ≥{CONSOLIDATION_MIN_NODES})",
                    artifacts={"fresh_node_count": len(fresh_nodes)},
                )

            # ── 2. Neighbors (1-hop)
            from synapse.graph.models import Edge
            from sqlalchemy import or_

            id_set = set(fresh_ids)
            edges_q = session.exec(
                select(Edge).where(
                    or_(
                        Edge.source_node_id.in_(id_set),  # type: ignore[attr-defined]
                        Edge.target_node_id.in_(id_set),  # type: ignore[attr-defined]
                    )
                )
            ).all()
            neighbor_ids: set[str] = set()
            for e in edges_q:
                for endpoint in (e.source_node_id, e.target_node_id):
                    if endpoint not in id_set:
                        neighbor_ids.add(endpoint)
            neighbors_payload: list[dict[str, Any]] = []
            if neighbor_ids:
                neighbor_rows = session.exec(
                    select(Node).where(Node.id.in_(neighbor_ids))  # type: ignore[attr-defined]
                ).all()
                for n in neighbor_rows:
                    neighbors_payload.append(
                        {
                            "type": n.type.value if hasattr(n.type, "value") else str(n.type),
                            "title": n.title,
                        }
                    )

            # ── 3. Existing INSIGHT titles (so we don't restate)
            existing = session.exec(
                select(Node).where(Node.type == NodeType.INSIGHT)
            ).all()
            existing_titles = [n.title for n in existing]

        prompt_context = {
            "lookback_hours": CONSOLIDATION_LOOKBACK_HOURS,
            "fresh_nodes": fresh_nodes,
            "neighbors": neighbors_payload,
            "existing_insights": existing_titles,
        }

        # ── 4. Call Claude (Opus — matches Synthesizer model) ─────────────────
        from pydantic import BaseModel as _PydBM, Field as _PydField

        class _Abstraction(_PydBM):
            principle: str
            supporting_node_titles: list[str] = _PydField(default_factory=list)
            domain_bridge: str | None = None
            novelty_confidence: float = _PydField(default=0.5, ge=0.0, le=1.0)

        class _ConsolidationOutput(_PydBM):
            confidence: float = _PydField(default=0.5, ge=0.0, le=1.0)
            summary: str = ""
            abstractions: list[_Abstraction] = _PydField(default_factory=list)

        try:
            result = await self._client.structured(
                prompt_file="consolidator.md",
                context=prompt_context,
                schema=_ConsolidationOutput,
                model=SYNTHESIZER_MODEL,
                agent="synthesizer-consolidate",
                temperature=0.5,
                max_tokens=3000,
            )
        except StructuredOutputError as exc:
            return AgentResult(
                agent="synthesizer-consolidate",
                ok=False,
                summary=f"consolidation call failed: {exc.last_error}",
                errors=[exc.last_error],
            )

        payload: _ConsolidationOutput = result.parsed  # type: ignore[assignment]

        # ── 5. Filter abstractions: must reference ≥2 supporting nodes
        kept: list[_Abstraction] = [
            a for a in payload.abstractions if len(a.supporting_node_titles) >= 2
        ]

        if not kept:
            return AgentResult(
                agent="synthesizer-consolidate",
                ok=True,
                summary=payload.summary or "no abstractions this cycle",
                artifacts={
                    "fresh_node_count": len(fresh_nodes),
                    "abstractions_proposed": 0,
                    "cost_usd": result.cost_usd,
                },
            )

        # ── 6. Append as INSIGHT candidates to pending_insights.md
        settings = get_settings()
        pending_path = settings.synapse_vault_path / LIBRARIAN_PENDING_INSIGHTS_FILE
        blocks = [
            _format_insight(
                _InsightCandidate(
                    description=a.principle,
                    node_titles=a.supporting_node_titles,
                )
            )
            for a in kept
        ]
        _append_pending(pending_path, f"From consolidation pass {now.date().isoformat()}", blocks)

        # ── 7. Hebbian: strengthen edges among the supporting nodes of each abstraction
        with Session(get_engine()) as session:
            for a in kept:
                ids: list[str] = []
                for title in a.supporting_node_titles:
                    row = session.exec(select(Node).where(Node.title == title)).first()
                    if row is not None:
                        ids.append(row.id)
                if len(ids) >= 2:
                    strengthen_edges(ids)

        return AgentResult(
            agent="synthesizer-consolidate",
            ok=True,
            summary=f"{len(kept)} abstraction(s) proposed",
            artifacts={
                "fresh_node_count": len(fresh_nodes),
                "abstractions_proposed": len(kept),
                "pending_insights_path": str(pending_path),
                "cost_usd": result.cost_usd,
            },
        )


synthesizer = Synthesizer()
