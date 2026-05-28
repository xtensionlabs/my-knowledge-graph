"""The Strategist — weekly planning + collision detection.

Runs Sundays at 18:00 local (PRD §6.3 + Appendix A.6) and on demand via
`synapse strategist run`. Surfaces:

    - Deadline collisions: EVENTs whose date overlaps a CONCEPT's next_review
    - Tradeoffs: when the user cannot do both, lay out the cost honestly
    - Synergy windows: BUILDs whose CS concepts overlap upcoming academic events
    - Open questions: QUESTION nodes that have aged past the freshness boundary

Opus 4.7 per `STRATEGIST_MODEL` — this is high-leverage reasoning that the
user will actually read; the Opus premium is worth it.

The Strategist does NOT mutate the graph. It writes a markdown report to
`${VAULT}/strategy/YYYY-MM-DD.md` and surfaces it via the CLI.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from synapse.agents.base import Agent, AgentResult
from synapse.config import (
    ANTHROPIC_MAX_TOKENS,
    STRATEGIST_COLLISION_LOOKAHEAD_HOURS,
    STRATEGIST_MODEL,
    SYNTHESIZER_OPEN_QUESTION_AGE_DAYS,
    get_settings,
)
from synapse.context.energy import current_energy
from synapse.graph.db import get_engine
from synapse.graph.hebbian import strengthen_edges
from synapse.graph.models import Node, NodeType
from synapse.llm.client import ClaudeClient, StructuredOutputError, claude


# ── Pydantic output schema ───────────────────────────────────────────────────


class _CollisionItem(BaseModel):
    description: str
    event_title: str
    event_date: str
    concept_titles: list[str] = Field(default_factory=list)
    severity: str = "medium"


class _TradeoffOption(BaseModel):
    label: str
    cost: str
    benefit: str


class _TradeoffItem(BaseModel):
    headline: str
    options: list[_TradeoffOption] = Field(default_factory=list)
    recommendation: str
    reasoning: str = ""


class _SynergyWindow(BaseModel):
    headline: str
    concept_title: str
    build_title: str
    action: str


class StrategistOutput(BaseModel):
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = ""
    collisions: list[_CollisionItem] = Field(default_factory=list)
    tradeoffs: list[_TradeoffItem] = Field(default_factory=list)
    synergy_windows: list[_SynergyWindow] = Field(default_factory=list)
    open_questions_to_resolve: list[str] = Field(default_factory=list)


# ── Context gathering ────────────────────────────────────────────────────────


def _assume_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _parse_event_date(node: Node) -> datetime | None:
    """Pull `_event_date=ISO` from the node's tags JSON (M2 encoding)."""
    try:
        tags = json.loads(node.tags or "[]")
    except json.JSONDecodeError:
        return None
    for t in tags:
        if isinstance(t, str) and t.startswith("_event_date="):
            iso = t[len("_event_date="):]
            try:
                return _assume_utc(datetime.fromisoformat(iso))
            except ValueError:
                return None
    return None


@dataclass
class StrategistContext:
    """Bundle of inputs the Strategist's prompt is given."""

    events: list[dict[str, Any]]
    due_concepts: list[dict[str, Any]]
    open_questions: list[dict[str, Any]]
    recent_builds: list[dict[str, Any]]
    energy: str


def detect_collisions(lookahead_hours: int) -> list[tuple[Node, list[Node]]]:
    """Find EVENTs whose date falls inside a CONCEPT's next_review window.

    Returns:
        List of (event_node, [colliding_concept_node, …]) tuples. A collision
        is defined as: the EVENT date is within {lookahead_hours} of the
        CONCEPT's next_review.

    The user can then ask the Strategist to reason about which side to favor.
    """
    now = _utcnow()
    horizon = now + timedelta(hours=lookahead_hours)

    with Session(get_engine()) as session:
        events = list(
            session.exec(select(Node).where(Node.type == NodeType.EVENT)).all()
        )
        concepts = list(
            session.exec(select(Node).where(Node.type == NodeType.CONCEPT)).all()
        )

    out: list[tuple[Node, list[Node]]] = []
    for e in events:
        date = _parse_event_date(e)
        if date is None or not (now <= date <= horizon):
            continue
        # Concepts whose next_review window straddles the event date.
        window_start = date - timedelta(hours=24)
        window_end = date + timedelta(hours=24)
        colliding = []
        for c in concepts:
            nr = _assume_utc(c.next_review)
            if nr is None:
                continue
            if window_start <= nr <= window_end:
                colliding.append(c)
        if colliding:
            out.append((e, colliding))
    return out


def _build_context(lookahead_hours: int) -> StrategistContext:
    now = _utcnow()
    horizon = now + timedelta(hours=lookahead_hours)

    with Session(get_engine()) as session:
        # Events
        all_events = list(session.exec(select(Node).where(Node.type == NodeType.EVENT)).all())
        events_payload: list[dict[str, Any]] = []
        for e in all_events:
            date = _parse_event_date(e)
            if date is None or not (now <= date <= horizon):
                continue
            try:
                tags = json.loads(e.tags or "[]")
            except json.JSONDecodeError:
                tags = []
            concept_titles = [t for t in tags if isinstance(t, str) and not t.startswith("_")]
            events_payload.append(
                {"title": e.title, "date": date.isoformat(), "concept_titles": concept_titles}
            )

        # Due concepts
        due_concepts: list[dict[str, Any]] = []
        for c in session.exec(select(Node).where(Node.type == NodeType.CONCEPT)).all():
            nr = _assume_utc(c.next_review)
            if nr is None:
                continue
            if nr <= horizon:
                due_concepts.append({"title": c.title, "next_review": nr.isoformat()})

        # Open questions (older than threshold days)
        oq_cutoff = now - timedelta(days=SYNTHESIZER_OPEN_QUESTION_AGE_DAYS)
        open_questions: list[dict[str, Any]] = []
        for q in session.exec(select(Node).where(Node.type == NodeType.QUESTION)).all():
            created = _assume_utc(q.created_at)
            if created is None or created > oq_cutoff:
                continue
            age = (now - created).days
            open_questions.append({"title": q.title, "age_days": age})

        # Recent builds (touched in last 7d)
        seven_days_ago = now - timedelta(days=7)
        recent_builds: list[dict[str, Any]] = []
        for b in session.exec(select(Node).where(Node.type == NodeType.BUILD)).all():
            updated = _assume_utc(b.updated_at)
            if updated is None or updated < seven_days_ago:
                continue
            summary = (b.content or "").splitlines()[0][:80] if b.content else ""
            recent_builds.append({"title": b.title, "summary": summary})

    return StrategistContext(
        events=events_payload,
        due_concepts=due_concepts,
        open_questions=open_questions,
        recent_builds=recent_builds,
        energy=current_energy(),
    )


# ── Markdown renderer ────────────────────────────────────────────────────────


def render_strategy_report(output: StrategistOutput, *, generated_at: datetime) -> str:
    """Render the JSON output as the user-facing markdown report."""
    lines: list[str] = []
    lines.append(f"# Weekly Strategy — {generated_at.date().isoformat()}")
    lines.append("")
    lines.append(f"_{output.summary}_" if output.summary else "_(no summary)_")
    lines.append("")
    lines.append(f"**Confidence:** {output.confidence:.2f}")
    lines.append("")

    lines.append("## Deadline collisions")
    if output.collisions:
        for c in output.collisions:
            lines.append(f"- **{c.event_title}** @ {c.event_date} — {c.description}")
            lines.append(f"  - Severity: {c.severity}")
            if c.concept_titles:
                lines.append(f"  - Concepts: {', '.join(c.concept_titles)}")
    else:
        lines.append("- (none — clear week)")
    lines.append("")

    lines.append("## Tradeoffs")
    if output.tradeoffs:
        for t in output.tradeoffs:
            lines.append(f"### {t.headline}")
            for opt in t.options:
                lines.append(f"- **{opt.label}** — gives up: {opt.cost} / keeps: {opt.benefit}")
            lines.append(f"**Recommendation:** {t.recommendation}")
            if t.reasoning:
                lines.append(f"_Why: {t.reasoning}_")
            lines.append("")
    else:
        lines.append("- (no live tradeoffs this week)")
        lines.append("")

    lines.append("## Synergy windows")
    if output.synergy_windows:
        for s in output.synergy_windows:
            lines.append(f"- **{s.headline}** — {s.concept_title} ↔ {s.build_title}")
            lines.append(f"  - Action: {s.action}")
    else:
        lines.append("- (none)")
    lines.append("")

    if output.open_questions_to_resolve:
        lines.append("## Open questions to resolve")
        for q in output.open_questions_to_resolve:
            lines.append(f"- {q}")
        lines.append("")

    return "\n".join(lines) + "\n"


# ── The agent ────────────────────────────────────────────────────────────────


class Strategist(Agent):
    """The Strategist agent — `name = strategist`."""

    name = "strategist"

    def __init__(self, *, client: ClaudeClient | None = None) -> None:
        self._client = client or claude

    async def run(  # type: ignore[override]
        self,
        *,
        lookahead_hours: int = STRATEGIST_COLLISION_LOOKAHEAD_HOURS,
    ) -> AgentResult:
        """Build context, call Opus, render the markdown report."""
        context = _build_context(lookahead_hours)
        prompt_context = {
            "lookahead_hours": lookahead_hours,
            "events": context.events,
            "due_concepts": context.due_concepts,
            "open_questions": context.open_questions,
            "recent_builds": context.recent_builds,
            "energy": context.energy,
        }
        try:
            result = await self._client.structured(
                prompt_file="strategist.md",
                context=prompt_context,
                schema=StrategistOutput,
                model=STRATEGIST_MODEL,
                agent=self.name,
                temperature=0.3,
                max_tokens=ANTHROPIC_MAX_TOKENS,
            )
        except StructuredOutputError as exc:
            return AgentResult(
                agent=self.name,
                ok=False,
                summary=f"strategist call failed: {exc.last_error}",
                errors=[exc.last_error],
            )

        output: StrategistOutput = result.parsed  # type: ignore[assignment]
        now = _utcnow()
        report_md = render_strategy_report(output, generated_at=now)

        # Write to vault/strategy/YYYY-MM-DD.md
        settings = get_settings()
        out_dir = settings.synapse_vault_path / "strategy"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{now.date().isoformat()}.md"
        out_path.write_text(report_md, encoding="utf-8")

        # Hebbian reinforcement: every concept that co-appears in a collision
        # or synergy gets its mutual edges strengthened.
        co_active_groups: list[list[str]] = []
        with Session(get_engine()) as session:
            for col in output.collisions:
                ids: list[str] = []
                for ctitle in col.concept_titles:
                    row = session.exec(
                        select(Node).where(Node.title == ctitle)
                    ).first()
                    if row is not None:
                        ids.append(row.id)
                if len(ids) >= 2:
                    co_active_groups.append(ids)
            for s in output.synergy_windows:
                ids = []
                for title in (s.concept_title, s.build_title):
                    row = session.exec(select(Node).where(Node.title == title)).first()
                    if row is not None:
                        ids.append(row.id)
                if len(ids) >= 2:
                    co_active_groups.append(ids)

        for group in co_active_groups:
            strengthen_edges(group)

        logger.info(
            "strategist: {n_col} collisions, {n_tr} tradeoffs, {n_syn} synergies",
            n_col=len(output.collisions),
            n_tr=len(output.tradeoffs),
            n_syn=len(output.synergy_windows),
        )

        return AgentResult(
            agent=self.name,
            ok=True,
            summary=output.summary or "strategist complete",
            artifacts={
                "report_path": str(out_path),
                "report_markdown": report_md,
                "collision_count": len(output.collisions),
                "tradeoff_count": len(output.tradeoffs),
                "synergy_count": len(output.synergy_windows),
                "confidence": output.confidence,
            },
        )


strategist = Strategist()
