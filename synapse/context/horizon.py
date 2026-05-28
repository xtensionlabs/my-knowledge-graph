"""Horizon queue — upcoming EVENTs and concept pre-loading.

Per PRD §6.3:
    - For each EVENT within `HORIZON_LOOKAHEAD_HOURS` (72h) we maintain a
      horizon entry in the session state.
    - When an EVENT is `HORIZON_PRELOAD_HOURS` (48h) away, the linked CONCEPT
      nodes have their `next_review` accelerated to ≤ `HORIZON_ACCELERATED_NEXT_REVIEW_HOURS`
      (24h) so the Synthesizer surfaces them tomorrow morning.

M2 supports manual EVENT entry via `synapse event add` and a CLI/API hook.
M4 wires Google Calendar to populate EVENTs automatically.

Event date storage: EVENT nodes carry their date in their markdown `content`
or vault frontmatter; for M2 we accept a parameter on `add_event()` and store
it on the Node's `tags` JSON as a special `_event_date` entry so we don't
need a new column. This is a deliberately small compromise — when M4 wires
calendar OAuth we'll add a proper `event_date` column via the first real
alembic migration.

# CLARIFY: EVENT date is currently encoded in tags JSON as `_event_date=ISO`;
# this is the minimum-change interpretation to avoid an M2 alembic migration.
# Promote to a real column at M4 when Calendar OAuth lands.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from loguru import logger
from sqlmodel import Session, select

from synapse.config import (
    HORIZON_ACCELERATED_NEXT_REVIEW_HOURS,
    HORIZON_LOOKAHEAD_HOURS,
    HORIZON_PRELOAD_HOURS,
)
from synapse.context.session import HorizonItem, get_session, save_session
from synapse.graph.db import get_engine
from synapse.graph.models import Edge, Node, NodeType
from synapse.graph.operations import create_edge, create_node, get_node, list_nodes
from synapse.graph.vault_sync import write_node_file
from synapse.utils.time import assume_utc as _assume_utc

EVENT_DATE_TAG_PREFIX = "_event_date="


# ── Helpers: encoding event date into node.tags JSON ─────────────────────────


def _encode_event_date(dt: datetime) -> str:
    """Tag value that carries the EVENT date."""
    return f"{EVENT_DATE_TAG_PREFIX}{dt.astimezone(timezone.utc).isoformat()}"




def _extract_event_date(node: Node) -> datetime | None:
    """Pull the encoded date from a node's tags JSON, or None."""
    try:
        tags = json.loads(node.tags or "[]")
    except json.JSONDecodeError:
        return None
    for tag in tags:
        if isinstance(tag, str) and tag.startswith(EVENT_DATE_TAG_PREFIX):
            try:
                return _assume_utc(datetime.fromisoformat(tag[len(EVENT_DATE_TAG_PREFIX):]))
            except ValueError:
                return None
    return None


# ── Public API ───────────────────────────────────────────────────────────────


def add_event(
    *,
    title: str,
    date: datetime,
    content: str = "",
    linked_concept_titles: Iterable[str] | None = None,
) -> Node:
    """Create (or return existing) EVENT node and link it to listed CONCEPTs.

    Args:
        title: Human-readable EVENT title (e.g., "ICS1104 CAT").
        date: When the event happens. Will be converted to UTC for storage.
        content: Optional markdown body (e.g., scope of CAT, room, etc.).
        linked_concept_titles: CONCEPT node titles to pre-load before this event.
            Titles are matched case-insensitively against existing nodes.

    Returns:
        The EVENT node.
    """
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    date_tag = _encode_event_date(date)

    node = create_node(
        type=NodeType.EVENT,
        title=title,
        content=content,
        tags=[date_tag],
    )
    # If the node already existed with a different date tag, update tags so the
    # newest date wins (avoid stale schedule).
    if not any(_extract_event_date(node) == date for _ in (0,)):
        from synapse.graph.operations import update_node

        # Strip prior `_event_date=` tags before adding the new one.
        existing_tags = [
            t for t in json.loads(node.tags or "[]")
            if not (isinstance(t, str) and t.startswith(EVENT_DATE_TAG_PREFIX))
        ]
        existing_tags.append(date_tag)
        # update_node merges tags; we need to replace, so go through the DB directly.
        with Session(get_engine()) as db:
            row = db.get(Node, node.id)
            if row is not None:
                row.tags = json.dumps(existing_tags)
                db.add(row)
                db.commit()
                db.refresh(row)
                node = row

    # Link to concepts.
    for ct in linked_concept_titles or []:
        from synapse.graph.operations import find_node_by_title

        concept = find_node_by_title(ct, type_=NodeType.CONCEPT)
        if concept is None:
            logger.info("horizon: skipping unknown concept {t!r} for event {e!r}", t=ct, e=title)
            continue
        try:
            create_edge(
                source_node_id=node.id,
                target_node_id=concept.id,
                relation_type="applies_to",
                created_by="user",
                note=f"pre-load for {title}",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("horizon: edge create failed: {exc}", exc=exc)

    write_node_file(node)
    refresh_horizon()
    return node


def list_upcoming_events(*, hours: int = HORIZON_LOOKAHEAD_HOURS) -> list[tuple[Node, datetime]]:
    """Return EVENT nodes occurring within the next `hours`, sorted by date."""
    now = datetime.now(tz=timezone.utc)
    cutoff = now + timedelta(hours=hours)
    candidates: list[tuple[Node, datetime]] = []
    for node in list_nodes(types=[NodeType.EVENT]):
        when = _extract_event_date(node)
        if when is None:
            continue
        if now <= when <= cutoff:
            candidates.append((node, when))
    candidates.sort(key=lambda nd: nd[1])
    return candidates


def _linked_concept_ids(event_node_id: str) -> list[str]:
    """Concepts linked from this EVENT (via any outgoing edge)."""
    with Session(get_engine()) as db:
        edges = list(db.exec(select(Edge).where(Edge.source_node_id == event_node_id)).all())
        concept_ids: list[str] = []
        for e in edges:
            target = db.get(Node, e.target_node_id)
            if target is not None and target.type == NodeType.CONCEPT:
                concept_ids.append(target.id)
        return concept_ids


def _accelerate_concept(node_id: str) -> bool:
    """Pull a concept's `next_review` in to ≤ 24h from now. Returns True if changed."""
    with Session(get_engine()) as db:
        node = db.get(Node, node_id)
        if node is None or node.type != NodeType.CONCEPT:
            return False
        now = datetime.now(tz=timezone.utc)
        cap = now + timedelta(hours=HORIZON_ACCELERATED_NEXT_REVIEW_HOURS)
        existing = _assume_utc(node.next_review)
        if existing is None or existing > cap:
            node.next_review = cap
            db.add(node)
            db.commit()
            logger.info("horizon: accelerated next_review for {id}", id=node_id)
            return True
        return False


def refresh_horizon() -> int:
    """Recompute the Horizon section of the session state.

    Walks all upcoming EVENTs, builds HorizonItems, triggers pre-load
    acceleration on any EVENT inside `HORIZON_PRELOAD_HOURS`. Returns the
    number of EVENTs in the queue.
    """
    upcoming = list_upcoming_events()
    snap = get_session()
    snap.horizon = []
    now = datetime.now(tz=timezone.utc)
    for event_node, when in upcoming:
        concept_ids = _linked_concept_ids(event_node.id)
        hours_out = (when - now).total_seconds() / 3600.0
        preload = hours_out <= HORIZON_PRELOAD_HOURS
        if preload:
            for cid in concept_ids:
                _accelerate_concept(cid)
        snap.horizon.append(
            HorizonItem(
                event_node_id=event_node.id,
                title=event_node.title,
                date=when.isoformat(),
                prep_concept_ids=concept_ids,
                preload_triggered=preload,
            )
        )
    save_session(snap)
    return len(snap.horizon)
