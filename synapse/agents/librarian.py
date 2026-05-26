"""The Librarian — inbox → knowledge graph.

Runs every 2 hours (PRD §7.1) or on demand via `synapse librarian run`. For
each unprocessed item in `${VAULT}/inbox/`:

    1. Read the markdown + frontmatter.
    2. Render the Librarian prompt with the current graph snapshot.
    3. Call Claude Sonnet (`LIBRARIAN_MODEL`) with structured output.
    4. Validate the JSON against `LibrarianOutput` (Pydantic).
    5. Apply node creates/updates and edge creates to the graph.
    6. Write pending-review and pending-insight files for user confirmation.
    7. Move the inbox file into `${VAULT}/archive/` (never delete).
    8. After each item, refresh the graph snapshot so the next item sees the
       newly-created nodes for dedup + edge anchoring.

Hard guarantees enforced here, not in the prompt:

    - INSIGHT nodes are NEVER created automatically. The model can only put
      them in `insight_candidates`; we append those to `pending_insights.md`.
    - Confidence < `LIBRARIAN_CONFIDENCE_THRESHOLD` marks the node
      `needs_review=true`. We still create it — the rule is honesty, not
      gatekeeping.
    - Inbox files are MOVED (`os.replace`) into `archive/`, never deleted.
      If validation fails for an item, it is left in place and counted as an error.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from loguru import logger
from pydantic import BaseModel, Field

from synapse.agents.base import Agent, AgentResult
from synapse.agents.git_ingest import git_ingest_agent
from synapse.config import (
    LIBRARIAN_CONFIDENCE_THRESHOLD,
    LIBRARIAN_MAX_ITEMS_PER_RUN,
    LIBRARIAN_MODEL,
    LIBRARIAN_PENDING_INSIGHTS_FILE,
    LIBRARIAN_PENDING_REVIEW_FILE,
    get_settings,
)
from synapse.graph.models import NodeType
from synapse.graph.operations import (
    create_edge,
    create_node,
    find_node_by_title,
    list_nodes_summary,
    update_node,
)
from synapse.graph.retention import initial_state
from synapse.graph.vault_sync import write_node_file
from synapse.llm.client import ClaudeClient, StructuredOutputError, claude


# ── Pydantic schemas ─────────────────────────────────────────────────────────


class _NodeProposal(BaseModel):
    """One entry of `nodes_to_create`."""

    type: str
    title: str = Field(min_length=1)
    content: str = ""
    tags: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    startup_relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)


class _NodeUpdate(BaseModel):
    """One entry of `nodes_to_update`."""

    id: str
    content_addition: str = ""
    new_tags: list[str] = Field(default_factory=list)


class _EdgeProposal(BaseModel):
    """One entry of `edges_to_create`."""

    source_title: str = Field(min_length=1)
    target_title: str = Field(min_length=1)
    relation: str
    note: str | None = None
    weight: float = 1.0


class _StartupMirror(BaseModel):
    """Pending startup-mirror suggestion."""

    concept_title: str
    build_module: str
    reason: str


class _InsightCandidate(BaseModel):
    """Pending INSIGHT — never auto-created."""

    description: str
    node_titles: list[str] = Field(default_factory=list)


class LibrarianOutput(BaseModel):
    """The exact schema the Librarian prompt is contracted to return."""

    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = ""
    nodes_to_create: list[_NodeProposal] = Field(default_factory=list)
    nodes_to_update: list[_NodeUpdate] = Field(default_factory=list)
    edges_to_create: list[_EdgeProposal] = Field(default_factory=list)
    startup_mirror_suggestions: list[_StartupMirror] = Field(default_factory=list)
    insight_candidates: list[_InsightCandidate] = Field(default_factory=list)


# ── Item parsing ─────────────────────────────────────────────────────────────


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


@dataclass
class InboxItem:
    """An inbox file split into frontmatter + body."""

    path: Path
    frontmatter: dict[str, Any]
    body: str

    @property
    def capture_id(self) -> str:
        return str(self.frontmatter.get("id", self.path.stem))

    @property
    def source(self) -> str:
        return str(self.frontmatter.get("source", "unknown"))


def _parse_inbox_file(path: Path) -> InboxItem | None:
    """Parse a single inbox markdown file. Returns None on malformed input."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("cannot read inbox file {p}: {exc}", p=path, exc=exc)
        return None
    m = _FRONTMATTER_RE.match(text)
    if m is None:
        logger.warning("no frontmatter in {p}; skipping", p=path)
        return None
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        logger.warning("malformed frontmatter in {p}: {exc}", p=path, exc=exc)
        return None
    return InboxItem(path=path, frontmatter=fm, body=m.group(2).strip())


def _list_inbox_items(inbox_dir: Path, *, max_items: int) -> list[InboxItem]:
    """Return parsed, unprocessed inbox items, oldest first."""
    if not inbox_dir.exists():
        return []
    files = sorted(
        (p for p in inbox_dir.glob("*.md") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
    )[:max_items]
    items: list[InboxItem] = []
    for f in files:
        item = _parse_inbox_file(f)
        if item is None:
            continue
        if item.frontmatter.get("processed") is True:
            continue
        items.append(item)
    return items


# ── Apply graph mutations ────────────────────────────────────────────────────


def _coerce_node_type(value: str) -> NodeType | None:
    """Convert a string to NodeType. Returns None for invalid (caller logs+skips)."""
    try:
        return NodeType(value.upper())
    except ValueError:
        return None


def _apply_node_proposals(
    proposals: Iterable[_NodeProposal], *, source_id: str
) -> dict[str, str]:
    """Create proposed nodes. Returns title→id map for edge resolution.

    INSIGHT proposals are silently dropped here (caught by an earlier guard);
    they should already be in `insight_candidates`, not `nodes_to_create`.
    """
    title_to_id: dict[str, str] = {}
    for proposal in proposals:
        node_type = _coerce_node_type(proposal.type)
        if node_type is None:
            logger.warning("librarian proposed invalid type {t!r}", t=proposal.type)
            continue
        if node_type is NodeType.INSIGHT:
            logger.warning(
                "librarian tried to auto-create INSIGHT {title!r} — refused; routed to candidates",
                title=proposal.title,
            )
            continue
        try:
            node = create_node(
                type=node_type,
                title=proposal.title,
                content=proposal.content,
                source_ids=[source_id],
                tags=proposal.tags,
                needs_review=proposal.confidence < LIBRARIAN_CONFIDENCE_THRESHOLD,
                startup_relevance_score=proposal.startup_relevance_score,
            )
            # Initialize SM-2 state on new CONCEPT nodes so M2 has something to schedule.
            if node_type is NodeType.CONCEPT and node.review_count == 0:
                _apply_initial_retention(node.id)
            title_to_id[proposal.title.strip().lower()] = node.id
            write_node_file(node)
        except Exception as exc:  # noqa: BLE001 — log and keep going
            logger.exception("create_node failed for {title!r}: {exc}", title=proposal.title, exc=exc)
    return title_to_id


def _apply_initial_retention(node_id: str) -> None:
    """Populate SM-2 fields on a freshly created CONCEPT node."""
    from sqlmodel import Session
    from synapse.graph.db import get_engine
    from synapse.graph.models import Node

    state = initial_state()
    with Session(get_engine()) as session:
        node = session.get(Node, node_id)
        if node is None:
            return
        node.ease_factor = state.ease_factor
        node.interval_days = state.interval_days
        node.next_review = state.next_review
        node.last_reviewed = state.last_reviewed
        node.review_count = state.review_count
        session.add(node)
        session.commit()


def _apply_node_updates(updates: Iterable[_NodeUpdate], *, source_id: str) -> None:
    """Append content + tags to existing nodes."""
    for upd in updates:
        try:
            updated = update_node(
                upd.id,
                content_addition=upd.content_addition or None,
                new_source_ids=[source_id],
                new_tags=upd.new_tags or None,
            )
            write_node_file(updated)
        except Exception as exc:  # noqa: BLE001
            logger.warning("update_node failed for {id!r}: {exc}", id=upd.id, exc=exc)


def _apply_edge_proposals(
    edges: Iterable[_EdgeProposal],
    *,
    new_title_to_id: dict[str, str],
) -> int:
    """Resolve title→id for both endpoints and create edges. Returns count created."""
    created = 0
    for proposal in edges:
        source_id = _resolve_title(proposal.source_title, new_title_to_id)
        target_id = _resolve_title(proposal.target_title, new_title_to_id)
        if source_id is None or target_id is None:
            logger.info(
                "edge skipped: unresolved title(s) source={s!r} target={t!r}",
                s=proposal.source_title, t=proposal.target_title,
            )
            continue
        try:
            create_edge(
                source_node_id=source_id,
                target_node_id=target_id,
                relation_type=proposal.relation,
                weight=proposal.weight,
                created_by="librarian",
                note=proposal.note,
            )
            created += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "create_edge failed ({s} → {t}, {r}): {exc}",
                s=proposal.source_title, t=proposal.target_title, r=proposal.relation, exc=exc,
            )
    return created


def _resolve_title(title: str, new_title_to_id: dict[str, str]) -> str | None:
    """Resolve a title to a node id, checking both newly-created and existing nodes."""
    key = title.strip().lower()
    if key in new_title_to_id:
        return new_title_to_id[key]
    existing = find_node_by_title(title)
    return existing.id if existing else None


# ── Pending files (startup mirror + insights) ────────────────────────────────


def _isoformat_utc() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _append_pending(file: Path, header: str, blocks: list[str]) -> None:
    """Append blocks to a markdown file under a timestamped header."""
    if not blocks:
        return
    file.parent.mkdir(parents=True, exist_ok=True)
    stamp = _isoformat_utc()
    body = f"\n## {header} — {stamp}\n\n" + "\n".join(blocks) + "\n"
    with file.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(body)


def _format_startup_mirror(s: _StartupMirror) -> str:
    return f"- **{s.concept_title} ↔ {s.build_module}** — {s.reason}"


def _format_insight(i: _InsightCandidate) -> str:
    titles = ", ".join(i.node_titles) or "(none)"
    return f"- {i.description}\n  - Nodes: {titles}"


# ── Archive ──────────────────────────────────────────────────────────────────


def _archive_item(item: InboxItem, archive_dir: Path) -> Path:
    """Move an inbox file to `archive/`. NEVER delete (CLAUDE.md rule)."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / item.path.name
    # Mark `processed: true` in the archived file's frontmatter so a future
    # rerun of the Librarian on the archive doesn't reprocess.
    text = item.path.read_text(encoding="utf-8")
    text = text.replace("processed: false", "processed: true", 1)
    target.write_text(text, encoding="utf-8")
    item.path.unlink()  # remove from inbox; the archive copy is the canonical record
    return target


# ── INSIGHT promotion (M3) ──────────────────────────────────────────────────


@dataclass
class PendingInsight:
    """Parsed entry from `pending_insights.md`."""

    index: int          # 1-based position in the file
    description: str
    node_titles: list[str]
    raw_block: str      # the full markdown block (used for removal)


def parse_pending_insights(file: Path) -> list[PendingInsight]:
    """Parse `pending_insights.md` into a list of PendingInsight entries.

    The file format (written by `_append_pending`) is:

        ## From <source> — <timestamp>

        - <description>
          - Nodes: <title1>, <title2>

    Returns entries in file order.  Empty list if file absent/empty.
    """
    if not file.is_file():
        return []
    text = file.read_text(encoding="utf-8")
    results: list[PendingInsight] = []
    idx = 1
    # Each insight entry starts with "- " (list bullet after a blank line).
    # We collect blocks between bullets.
    for raw in re.split(r"\n(?=- )", text):
        raw = raw.strip()
        if not raw.startswith("- "):
            continue
        lines = raw.splitlines()
        description = lines[0].lstrip("- ").strip()
        node_titles: list[str] = []
        for line in lines[1:]:
            stripped = line.strip()
            if stripped.lower().startswith("- nodes:"):
                raw_titles = stripped[len("- nodes:"):].strip()
                node_titles = [t.strip() for t in raw_titles.split(",") if t.strip()]
        if description:
            results.append(
                PendingInsight(
                    index=idx,
                    description=description,
                    node_titles=node_titles,
                    raw_block=raw,
                )
            )
            idx += 1
    return results


def confirm_insight(entry: PendingInsight, file: Path) -> str:
    """Promote a pending INSIGHT candidate to a real INSIGHT node.

    Creates the node, links it to the referenced node titles, removes the
    entry from `pending_insights.md`.

    Args:
        entry:  The PendingInsight to promote.
        file:   Path to `pending_insights.md`.

    Returns:
        The new INSIGHT node's id.
    """
    node = create_node(
        type=NodeType.INSIGHT,
        title=entry.description[:120],  # trim to reasonable title length
        content=entry.description,
        source_ids=["user_confirmed"],
        tags=["confirmed"],
    )
    # Link to each referenced node.
    for title in entry.node_titles:
        ref = find_node_by_title(title)
        if ref is None:
            logger.info("insight confirm: referenced node {t!r} not found; skipping edge", t=title)
            continue
        try:
            create_edge(
                source_node_id=node.id,
                target_node_id=ref.id,
                relation_type="derived_from",
                weight=1.0,
                created_by="user",
                note="confirmed via `synapse insight confirm`",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("insight confirm: edge to {t!r} failed: {exc}", t=title, exc=exc)

    # Remove entry from pending_insights.md (re-write without that block).
    _remove_insight_entry(entry, file)
    write_node_file(node)
    return node.id


def _remove_insight_entry(entry: PendingInsight, file: Path) -> None:
    """Remove `entry.raw_block` from the pending insights file."""
    if not file.is_file():
        return
    text = file.read_text(encoding="utf-8")
    # Replace the raw block (preceded by optional whitespace / newlines) with empty.
    updated = text.replace("\n" + entry.raw_block, "", 1)
    if updated == text:
        # Try without leading newline (edge case: first entry in section).
        updated = text.replace(entry.raw_block, "", 1)
    file.write_text(updated, encoding="utf-8")


# ── The agent ────────────────────────────────────────────────────────────────


@dataclass
class _RunStats:
    items_processed: int = 0
    items_failed: int = 0
    nodes_created: int = 0
    nodes_updated: int = 0
    edges_created: int = 0
    insights_pending: int = 0
    mirrors_pending: int = 0
    low_confidence_nodes: int = 0


class Librarian(Agent):
    """The Librarian agent — `name = librarian`."""

    name = "librarian"

    def __init__(self, *, client: ClaudeClient | None = None) -> None:
        self._client = client or claude

    async def run(  # type: ignore[override]
        self,
        *,
        max_items: int = LIBRARIAN_MAX_ITEMS_PER_RUN,
    ) -> AgentResult:
        """Process up to `max_items` inbox items.

        Returns:
            AgentResult with summary statistics in `artifacts`.
        """
        settings = get_settings()
        items = _list_inbox_items(settings.inbox_dir, max_items=max_items)
        stats = _RunStats()
        errors: list[str] = []

        if not items:
            return AgentResult(
                agent=self.name,
                ok=True,
                summary="inbox empty — nothing to do",
                artifacts={"items_processed": 0},
            )

        pending_review = settings.synapse_vault_path / LIBRARIAN_PENDING_REVIEW_FILE
        pending_insights = settings.synapse_vault_path / LIBRARIAN_PENDING_INSIGHTS_FILE

        for item in items:
            # ── Route: git items are handled deterministically (no LLM) ──────
            if item.source == "git":
                try:
                    result = git_ingest_agent.process_item(
                        frontmatter=item.frontmatter,
                        body=item.body,
                        capture_id=item.capture_id,
                    )
                    if result.ok:
                        stats.nodes_created += 1  # one BUILD node created/updated
                        stats.edges_created += result.edges_created
                        _archive_item(item, settings.archive_dir)
                        stats.items_processed += 1
                    else:
                        stats.items_failed += 1
                        errors.append(f"{item.path.name}: git_ingest: {result.error}")
                except Exception as exc:  # noqa: BLE001
                    stats.items_failed += 1
                    errors.append(f"{item.path.name}: git_ingest unexpected: {exc}")
                    logger.exception("librarian: git_ingest failed for {f}", f=item.path.name)
                continue

            # ── Default path: Claude-based extraction ────────────────────────
            try:
                output = await self._process_one(item)
            except StructuredOutputError as exc:
                stats.items_failed += 1
                errors.append(f"{item.path.name}: structured-output failure: {exc.last_error}")
                logger.error(
                    "librarian: failed to process {f}: {err}",
                    f=item.path.name, err=exc.last_error,
                )
                continue
            except Exception as exc:  # noqa: BLE001
                stats.items_failed += 1
                errors.append(f"{item.path.name}: {exc}")
                logger.exception("librarian: unexpected error on {f}", f=item.path.name)
                continue

            # Apply mutations.
            title_to_id = _apply_node_proposals(output.nodes_to_create, source_id=item.capture_id)
            stats.nodes_created += len(title_to_id)
            stats.low_confidence_nodes += sum(
                1 for p in output.nodes_to_create if p.confidence < LIBRARIAN_CONFIDENCE_THRESHOLD
            )

            _apply_node_updates(output.nodes_to_update, source_id=item.capture_id)
            stats.nodes_updated += len(output.nodes_to_update)

            stats.edges_created += _apply_edge_proposals(
                output.edges_to_create, new_title_to_id=title_to_id
            )

            # Pending files.
            mirror_blocks = [_format_startup_mirror(s) for s in output.startup_mirror_suggestions]
            insight_blocks = [_format_insight(i) for i in output.insight_candidates]
            _append_pending(pending_review, f"From {item.path.name}", mirror_blocks)
            _append_pending(pending_insights, f"From {item.path.name}", insight_blocks)
            stats.mirrors_pending += len(mirror_blocks)
            stats.insights_pending += len(insight_blocks)

            # Archive.
            _archive_item(item, settings.archive_dir)
            stats.items_processed += 1

        summary = (
            f"processed {stats.items_processed}/{len(items)} items; "
            f"+{stats.nodes_created} nodes, +{stats.edges_created} edges, "
            f"{stats.insights_pending} insights pending, "
            f"{stats.mirrors_pending} mirrors pending"
        )

        return AgentResult(
            agent=self.name,
            ok=stats.items_failed == 0,
            summary=summary,
            artifacts={
                "items_processed": stats.items_processed,
                "items_failed": stats.items_failed,
                "nodes_created": stats.nodes_created,
                "nodes_updated": stats.nodes_updated,
                "edges_created": stats.edges_created,
                "insights_pending": stats.insights_pending,
                "mirrors_pending": stats.mirrors_pending,
                "low_confidence_nodes": stats.low_confidence_nodes,
            },
            errors=errors,
        )

    async def _process_one(self, item: InboxItem) -> LibrarianOutput:
        """Run Claude on a single inbox item and return the validated payload."""
        # Fresh snapshot each loop so previously-created nodes inform the next item.
        existing = list_nodes_summary()
        context = {
            "capture_body": item.body,
            "capture_frontmatter_yaml": yaml.safe_dump(
                item.frontmatter, sort_keys=False, allow_unicode=True
            ),
            "existing_nodes": [
                {"id": n.id, "type": n.type, "title": n.title} for n in existing
            ],
        }
        result = await self._client.structured(
            prompt_file="librarian.md",
            context=context,
            schema=LibrarianOutput,
            model=LIBRARIAN_MODEL,
            agent=self.name,
            temperature=0.3,
            max_tokens=4096,
        )
        return result.parsed  # type: ignore[return-value]


librarian = Librarian()
