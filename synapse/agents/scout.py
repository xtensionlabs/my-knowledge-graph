"""The Scout — weekly external-signal filter.

Runs Saturday 10:00 local (PRD §7.4). Reads items the user queued for triage
(via `synapse scout add` or the browser extension in M5b) and ranks them
against the active graph. Output: a `vault/scout/YYYY-MM-DD.md` digest of
items worth reading.

Model: Sonnet 4.5 (`SCOUT_MODEL`) — modest volume, modest reasoning depth.
"""

from __future__ import annotations

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
    SCOUT_MAX_ITEMS_PER_RUN,
    SCOUT_MODEL,
    SCOUT_RELEVANCE_THRESHOLD,
    SYNTHESIZER_OPEN_QUESTION_AGE_DAYS,
    get_settings,
)
from synapse.graph.db import get_engine
from synapse.graph.models import Node, NodeType
from synapse.llm.client import ClaudeClient, StructuredOutputError, claude

SCOUT_QUEUE_FILENAME = "scout_queue.md"


# ── Pydantic schema ──────────────────────────────────────────────────────────


class _KeptItem(BaseModel):
    title: str
    url: str = ""
    relevance_score: float = Field(ge=0.0, le=1.0)
    matches_concepts: list[str] = Field(default_factory=list)
    matches_builds: list[str] = Field(default_factory=list)
    matches_questions: list[str] = Field(default_factory=list)
    one_line_why: str


class ScoutOutput(BaseModel):
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = ""
    kept: list[_KeptItem] = Field(default_factory=list)
    dropped_count: int = 0
    drop_reasons_summary: str = ""


# ── Queue helpers ────────────────────────────────────────────────────────────


@dataclass
class ScoutItem:
    """One item in the scout queue."""

    title: str
    source: str = "manual"
    url: str = ""
    summary: str = ""


def add_to_queue(item: ScoutItem) -> Path:
    """Append an item to the user's scout queue file. Returns the path."""
    settings = get_settings()
    queue_dir = settings.synapse_vault_path / "scout"
    queue_dir.mkdir(parents=True, exist_ok=True)
    path = queue_dir / SCOUT_QUEUE_FILENAME
    block = (
        f"\n## {datetime.now(tz=timezone.utc).isoformat()}\n"
        f"- title: {item.title}\n"
        f"- source: {item.source}\n"
        f"- url: {item.url}\n"
        f"- summary: {item.summary}\n"
    )
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(block)
    return path


def _read_queue(path: Path, *, max_items: int) -> list[ScoutItem]:
    """Parse the queue file into a list of ScoutItems."""
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    items: list[ScoutItem] = []
    current: dict[str, str] | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            if current is not None and current.get("title"):
                items.append(
                    ScoutItem(
                        title=current.get("title", ""),
                        source=current.get("source", "manual"),
                        url=current.get("url", ""),
                        summary=current.get("summary", ""),
                    )
                )
            current = {}
            continue
        if current is None:
            continue
        if line.startswith("- title:"):
            current["title"] = line[len("- title:"):].strip()
        elif line.startswith("- source:"):
            current["source"] = line[len("- source:"):].strip()
        elif line.startswith("- url:"):
            current["url"] = line[len("- url:"):].strip()
        elif line.startswith("- summary:"):
            current["summary"] = line[len("- summary:"):].strip()
    if current is not None and current.get("title"):
        items.append(
            ScoutItem(
                title=current.get("title", ""),
                source=current.get("source", "manual"),
                url=current.get("url", ""),
                summary=current.get("summary", ""),
            )
        )
    return items[:max_items]


def _clear_queue(path: Path) -> None:
    """Archive the queue (move to scout/archive/YYYY-MM-DD.md) after processing."""
    if not path.is_file():
        return
    archive = path.parent / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    target = archive / f"{datetime.now(tz=timezone.utc).date().isoformat()}.md"
    if target.exists():
        # Append to existing day's archive.
        target.write_text(
            target.read_text(encoding="utf-8") + "\n" + path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    else:
        path.replace(target)
    # Recreate empty queue.
    path.write_text("", encoding="utf-8")


# ── Context gathering ────────────────────────────────────────────────────────


def _gather_graph_context() -> dict[str, list[str]]:
    """Pull the user's active CONCEPTs, BUILDs, and open QUESTIONs."""
    now = datetime.now(tz=timezone.utc)
    oq_cutoff = now - timedelta(days=SYNTHESIZER_OPEN_QUESTION_AGE_DAYS)
    with Session(get_engine()) as session:
        concepts = [n.title for n in session.exec(
            select(Node).where(Node.type == NodeType.CONCEPT)
        ).all()]
        builds = [n.title for n in session.exec(
            select(Node).where(Node.type == NodeType.BUILD)
        ).all()]
        questions = []
        for q in session.exec(select(Node).where(Node.type == NodeType.QUESTION)).all():
            created = q.created_at
            if created is None:
                continue
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created <= oq_cutoff:
                questions.append(q.title)
    return {"concepts": concepts, "builds": builds, "questions": questions}


# ── Markdown digest ──────────────────────────────────────────────────────────


def render_scout_digest(output: ScoutOutput, *, generated_at: datetime) -> str:
    """Render the user-facing markdown digest."""
    lines: list[str] = []
    lines.append(f"# Scout digest — {generated_at.date().isoformat()}")
    lines.append("")
    lines.append(f"_{output.summary}_" if output.summary else "")
    lines.append("")
    lines.append(f"**Kept:** {len(output.kept)}  •  **Dropped:** {output.dropped_count}")
    if output.drop_reasons_summary:
        lines.append(f"_Drop reasons: {output.drop_reasons_summary}_")
    lines.append("")

    if output.kept:
        lines.append("## Items worth your attention")
        for k in output.kept:
            lines.append(f"### {k.title}")
            if k.url:
                lines.append(f"- {k.url}")
            lines.append(f"- **Why:** {k.one_line_why}")
            if k.matches_concepts:
                lines.append(f"- Matches concepts: {', '.join(k.matches_concepts)}")
            if k.matches_builds:
                lines.append(f"- Matches builds: {', '.join(k.matches_builds)}")
            if k.matches_questions:
                lines.append(f"- Answers questions: {', '.join(k.matches_questions)}")
            lines.append("")
    else:
        lines.append("## Nothing worth your attention this week\n")

    return "\n".join(lines) + "\n"


# ── The agent ────────────────────────────────────────────────────────────────


class Scout(Agent):
    """The Scout agent — `name = scout`."""

    name = "scout"

    def __init__(self, *, client: ClaudeClient | None = None) -> None:
        self._client = client or claude

    async def run(self) -> AgentResult:  # type: ignore[override]
        settings = get_settings()
        queue_path = settings.synapse_vault_path / "scout" / SCOUT_QUEUE_FILENAME
        items = _read_queue(queue_path, max_items=SCOUT_MAX_ITEMS_PER_RUN)
        if not items:
            return AgentResult(
                agent=self.name,
                ok=True,
                summary="queue empty — nothing to triage",
                artifacts={"items": 0},
            )

        ctx = _gather_graph_context()
        prompt_context = {
            "items": [
                {"title": it.title, "source": it.source, "url": it.url, "summary": it.summary}
                for it in items
            ],
            "concepts": ctx["concepts"],
            "builds": ctx["builds"],
            "questions": ctx["questions"],
            "relevance_threshold": SCOUT_RELEVANCE_THRESHOLD,
        }
        try:
            result = await self._client.structured(
                prompt_file="scout.md",
                context=prompt_context,
                schema=ScoutOutput,
                model=SCOUT_MODEL,
                agent=self.name,
                temperature=0.3,
                max_tokens=ANTHROPIC_MAX_TOKENS,
            )
        except StructuredOutputError as exc:
            return AgentResult(
                agent=self.name,
                ok=False,
                summary=f"scout call failed: {exc.last_error}",
                errors=[exc.last_error],
            )
        output: ScoutOutput = result.parsed  # type: ignore[assignment]

        # Enforce threshold AFTER the LLM — never trust the model to gate itself.
        kept = [k for k in output.kept if k.relevance_score >= SCOUT_RELEVANCE_THRESHOLD]
        output.kept = kept

        now = datetime.now(tz=timezone.utc)
        digest = render_scout_digest(output, generated_at=now)
        out_dir = settings.synapse_vault_path / "scout"
        out_dir.mkdir(parents=True, exist_ok=True)
        digest_path = out_dir / f"{now.date().isoformat()}.md"
        digest_path.write_text(digest, encoding="utf-8")

        _clear_queue(queue_path)

        logger.info(
            "scout: {kept} kept / {dropped} dropped from {total} items",
            kept=len(kept), dropped=output.dropped_count, total=len(items),
        )

        return AgentResult(
            agent=self.name,
            ok=True,
            summary=(
                f"{len(kept)} kept / {output.dropped_count} dropped"
                + (f" — {output.summary}" if output.summary else "")
            ),
            artifacts={
                "digest_path": str(digest_path),
                "digest_markdown": digest,
                "kept_count": len(kept),
                "dropped_count": output.dropped_count,
                "confidence": output.confidence,
                "cost_usd": result.cost_usd,
            },
        )


scout = Scout()
