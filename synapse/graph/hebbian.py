"""Hebbian edge dynamics — PRD Appendix A.1.

*Neurons that fire together wire together.* When agents co-activate ≥2 nodes
in one logical context (Librarian extraction, Synthesizer briefing, Strategist
reasoning, manual review), the edges between those nodes are strengthened.
Edges that are never co-activated decay over time.

Three public APIs:

- `strengthen_edges(node_ids, factor=...)` — call after any agent run that touched
  multiple nodes. Bumps `Edge.weight` and updates `last_strengthened`.
- `decay_old_edges()` — nightly scheduler job. Multiplicatively decays weights
  for edges whose grace period has elapsed.
- `list_weak_edges(threshold=...)` — surface candidates for manual pruning. We
  never auto-delete (CLAUDE.md rule).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from loguru import logger
from sqlmodel import Session, select

from synapse.config import (
    HEBBIAN_DECAY_FACTOR,
    HEBBIAN_DECAY_GRACE_DAYS,
    HEBBIAN_STRENGTHEN_FACTOR,
    HEBBIAN_WEAK_EDGE_THRESHOLD,
    HEBBIAN_WEIGHT_CEILING,
    HEBBIAN_WEIGHT_FLOOR,
)
from synapse.graph.db import get_engine
from synapse.graph.models import Edge
from synapse.utils.time import assume_utc as _assume_utc, utcnow as _utcnow


@dataclass
class StrengthenResult:
    """Outcome of one strengthen_edges call."""

    edges_strengthened: int
    edges_at_ceiling: int


@dataclass
class DecayResult:
    """Outcome of one decay_old_edges run."""

    edges_examined: int
    edges_decayed: int
    edges_at_floor: int


def strengthen_edges(
    node_ids: list[str],
    *,
    factor: float = HEBBIAN_STRENGTHEN_FACTOR,
) -> StrengthenResult:
    """Strengthen every edge whose endpoints are both in `node_ids`.

    Bumps `weight` additively (capped at `HEBBIAN_WEIGHT_CEILING`) and updates
    `last_strengthened` to now. Symmetric: both directions are strengthened.

    Args:
        node_ids: Nodes that co-activated in one logical context.
        factor:   Additive weight bump (default from config).

    Returns:
        StrengthenResult — useful for tests + logging.
    """
    if len(node_ids) < 2:
        return StrengthenResult(0, 0)

    id_set = set(node_ids)
    now = _utcnow()
    strengthened = 0
    at_ceiling = 0

    with Session(get_engine()) as session:
        # Pull every edge whose source OR target is in node_ids; filter to "both" in Python.
        candidates = session.exec(
            select(Edge).where(
                (Edge.source_node_id.in_(id_set)) | (Edge.target_node_id.in_(id_set))  # type: ignore[attr-defined]
            )
        ).all()
        for edge in candidates:
            if edge.source_node_id not in id_set or edge.target_node_id not in id_set:
                continue
            new_weight = min(edge.weight + factor, HEBBIAN_WEIGHT_CEILING)
            if new_weight >= HEBBIAN_WEIGHT_CEILING:
                at_ceiling += 1
            edge.weight = new_weight
            edge.last_strengthened = now
            session.add(edge)
            strengthened += 1
        if strengthened:
            session.commit()

    if strengthened:
        logger.debug(
            "hebbian: strengthened {n} edges ({ceil} at ceiling)",
            n=strengthened,
            ceil=at_ceiling,
        )
    return StrengthenResult(edges_strengthened=strengthened, edges_at_ceiling=at_ceiling)


def decay_old_edges(
    *,
    grace_days: int = HEBBIAN_DECAY_GRACE_DAYS,
    decay_factor: float = HEBBIAN_DECAY_FACTOR,
) -> DecayResult:
    """Multiplicatively decay edges that haven't been strengthened recently.

    For each edge whose `last_strengthened` (or `created_at` if null) is older
    than `grace_days`, multiply weight by `decay_factor`. Floor at
    `HEBBIAN_WEIGHT_FLOOR`. Never deletes.

    Returns:
        DecayResult with examined / decayed / at-floor counts.
    """
    cutoff = _utcnow() - timedelta(days=grace_days)
    examined = 0
    decayed = 0
    at_floor = 0

    with Session(get_engine()) as session:
        all_edges = list(session.exec(select(Edge)).all())
        for edge in all_edges:
            examined += 1
            anchor = edge.last_strengthened or edge.created_at
            if anchor is None:
                continue
            if _assume_utc(anchor) > cutoff:
                continue  # within grace period
            new_weight = max(edge.weight * decay_factor, HEBBIAN_WEIGHT_FLOOR)
            if new_weight == edge.weight:
                continue
            edge.weight = new_weight
            session.add(edge)
            decayed += 1
            if new_weight <= HEBBIAN_WEIGHT_FLOOR:
                at_floor += 1
        if decayed:
            session.commit()

    logger.info(
        "hebbian decay: examined={ex} decayed={d} at_floor={f}",
        ex=examined,
        d=decayed,
        f=at_floor,
    )
    return DecayResult(edges_examined=examined, edges_decayed=decayed, edges_at_floor=at_floor)


def list_weak_edges(*, threshold: float = HEBBIAN_WEAK_EDGE_THRESHOLD) -> list[Edge]:
    """Return edges whose weight has fallen below `threshold`, oldest first.

    The user reviews these via `synapse graph weak-edges`. We never auto-delete.
    """
    with Session(get_engine()) as session:
        rows = session.exec(
            select(Edge).where(Edge.weight < threshold)
        ).all()
    rows = sorted(
        rows,
        key=lambda e: _assume_utc(e.last_strengthened or e.created_at or _utcnow()),
    )
    return list(rows)
