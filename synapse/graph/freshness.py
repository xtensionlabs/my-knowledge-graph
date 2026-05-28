"""Freshness scores — PRD Appendix A.3 (Forgetting as Feature).

Each node has a derived freshness in [0.0, 1.0] computed from how recently it
was *touched*. "Touched" means any of: created_at, updated_at, last_reviewed
(CONCEPTs only), or any incoming/outgoing edge was strengthened recently.

Freshness has no schema column — it's a pure derivation from existing
timestamps, computed on demand. The search ranker mixes it in alongside
semantic similarity and centrality.

Nothing is ever deleted on the basis of low freshness. Cold nodes are simply
ranked lower and surfaced for manual review via `synapse graph cold`.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, select

from synapse.config import FORGETTING_HORIZON_DAYS
from synapse.graph.db import get_engine
from synapse.graph.models import Edge, Node
from synapse.utils.time import assume_utc as _assume_utc, utcnow as _utcnow


def _last_touched_for_node(
    node: Node, edge_touch_map: dict[str, datetime]
) -> datetime:
    """Most recent of: created_at, updated_at, last_reviewed, edge-strengthened."""
    candidates: list[datetime] = []
    for ts in (node.created_at, node.updated_at, node.last_reviewed):
        ts_utc = _assume_utc(ts)
        if ts_utc is not None:
            candidates.append(ts_utc)
    edge_ts = edge_touch_map.get(node.id)
    if edge_ts is not None:
        candidates.append(edge_ts)
    if not candidates:
        return _utcnow()
    return max(candidates)


def _freshness_from(last_touched: datetime, *, now: datetime, horizon_days: int) -> float:
    """Linear decay from 1.0 at touch-time → 0.0 at horizon. Clipped to [0, 1]."""
    age_days = (now - last_touched).total_seconds() / 86400.0
    if age_days <= 0:
        return 1.0
    if age_days >= horizon_days:
        return 0.0
    return max(0.0, min(1.0, 1.0 - (age_days / horizon_days)))


def compute_freshness_map(
    *, horizon_days: int = FORGETTING_HORIZON_DAYS
) -> dict[str, float]:
    """Return `node_id -> freshness` for every node in the graph.

    Args:
        horizon_days: Days over which a node decays from 1.0 to 0.0.
    """
    now = _utcnow()
    with Session(get_engine()) as session:
        nodes = list(session.exec(select(Node)).all())
        edges = list(session.exec(select(Edge)).all())

    # Build per-node "most recent edge strengthening" map.
    edge_touch: dict[str, datetime] = {}
    for e in edges:
        ts = _assume_utc(e.last_strengthened) or _assume_utc(e.created_at)
        if ts is None:
            continue
        for endpoint in (e.source_node_id, e.target_node_id):
            prev = edge_touch.get(endpoint)
            if prev is None or ts > prev:
                edge_touch[endpoint] = ts

    result: dict[str, float] = {}
    for n in nodes:
        last = _last_touched_for_node(n, edge_touch)
        result[n.id] = _freshness_from(last, now=now, horizon_days=horizon_days)
    return result


def list_cold_nodes(*, threshold: float, limit: int | None = None) -> list[tuple[Node, float]]:
    """Return (Node, freshness) pairs for nodes with freshness < threshold, coldest first.

    Args:
        threshold: Freshness ceiling (exclusive). Default from config.
        limit: Optional cap on returned rows.
    """
    fresh = compute_freshness_map()
    cold_ids = [nid for nid, f in fresh.items() if f < threshold]
    if not cold_ids:
        return []
    with Session(get_engine()) as session:
        nodes = list(session.exec(select(Node).where(Node.id.in_(cold_ids))).all())  # type: ignore[attr-defined]
    paired = [(n, fresh[n.id]) for n in nodes]
    paired.sort(key=lambda p: p[1])  # coldest first
    if limit is not None:
        paired = paired[:limit]
    return paired
