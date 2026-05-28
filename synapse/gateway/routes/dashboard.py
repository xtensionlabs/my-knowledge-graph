"""Dashboard API — endpoints consumed by the M5b Next.js UI.

Conventions:
    - Responses are JSON with a stable shape (the dashboard is the consumer).
    - Mostly read; the only write is POST /librarian/run, which triggers an
      existing agent rather than mutating the graph directly.
    - Datetimes serialized as ISO-8601 strings (Python `.isoformat()`).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlmodel import Session, desc, func, select

from synapse.config import (
    COMMUNITIES_HUB_TOP_K,
    COMMUNITIES_MIN_SIZE,
    DASHBOARD_API_KEY_HEADER,
    get_settings,
)
from synapse.graph.communities import detect_communities
from synapse.graph.db import get_engine
from synapse.graph.freshness import compute_freshness_map
from synapse.graph.models import ApiUsage, CaptureLog, Edge, Node, NodeType
from synapse.graph.operations import (
    build_networkx_graph,
    compute_centrality,
    graph_stats,
)
from synapse.utils.time import utcnow as _utcnow


def _require_api_key(
    x_synapse_api_key: str | None = Header(default=None, alias=DASHBOARD_API_KEY_HEADER),
) -> None:
    """Reject dashboard requests that don't carry the configured browser API key."""
    expected = get_settings().synapse_browser_api_key
    if not expected:
        # No key configured = open access (localhost-only deployments).
        return
    if not x_synapse_api_key or x_synapse_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid x-synapse-api-key",
        )


router = APIRouter(dependencies=[Depends(_require_api_key)])


@router.get("/overview")
def dashboard_overview() -> dict[str, Any]:
    """Snapshot of the most recent system state — counts + recent activity."""
    now = _utcnow()
    twenty_four_h_ago = now - timedelta(hours=24)
    stats = graph_stats()

    with Session(get_engine()) as session:
        recent_captures = int(session.exec(
            select(func.count(CaptureLog.id)).where(CaptureLog.created_at >= twenty_four_h_ago)
        ).one() or 0)
        total_captures = int(session.exec(select(func.count(CaptureLog.id))).one() or 0)
        recent_runs = list(session.exec(
            select(ApiUsage)
            .where(ApiUsage.created_at >= twenty_four_h_ago)
            .order_by(desc(ApiUsage.created_at))
            .limit(20)
        ).all())

    return {
        "generated_at": now.isoformat(),
        "graph": {
            "nodes": stats["node_count"],
            "edges": stats["edge_count"],
            "nodes_by_type": stats["nodes_by_type"],
            "orphans": stats["orphan_count"],
            "needs_review": stats["needs_review"],
        },
        "capture": {
            "total": total_captures,
            "last_24h": recent_captures,
        },
        "recent_agent_runs": [
            {
                "agent": r.agent,
                "model": r.model,
                "succeeded": r.succeeded,
                "cost_usd": r.cost_usd,
                "latency_ms": r.latency_ms,
                "at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in recent_runs
        ],
    }


@router.get("/graph")
def dashboard_graph(
    types: str | None = Query(None, description="Comma-separated node types to filter."),
    limit: int = Query(500, ge=1, le=5000, description="Cap on node count."),
) -> dict[str, Any]:
    """Nodes + edges for graph visualization.

    Includes derived freshness + centrality so the UI can render without a
    second round-trip. Edges include `weight` so Hebbian-strengthened
    connections can be rendered thicker.
    """
    type_filter: list[str] | None = None
    if types:
        type_filter = [t.strip().upper() for t in types.split(",") if t.strip()]

    with Session(get_engine()) as session:
        node_query = select(Node)
        if type_filter:
            node_query = node_query.where(Node.type.in_(type_filter))  # type: ignore[attr-defined]
        nodes = list(session.exec(node_query.limit(limit)).all())
        node_id_set = {n.id for n in nodes}

        # Only fetch edges where BOTH endpoints are in the filtered node set.
        edge_query = select(Edge).where(
            Edge.source_node_id.in_(node_id_set)  # type: ignore[attr-defined]
        )
        edges = [
            e for e in session.exec(edge_query).all()
            if e.target_node_id in node_id_set
        ]

    centrality = compute_centrality(build_networkx_graph())
    freshness = compute_freshness_map()

    return {
        "nodes": [
            {
                "id": n.id,
                "type": n.type.value if hasattr(n.type, "value") else str(n.type),
                "title": n.title,
                "centrality": float(centrality.get(n.id, 0.0)),
                "freshness": float(freshness.get(n.id, 1.0)),
                "needs_review": bool(n.needs_review),
            }
            for n in nodes
        ],
        "edges": [
            {
                "id": e.id,
                "source": e.source_node_id,
                "target": e.target_node_id,
                "relation": e.relation_type,
                "weight": float(e.weight),
                "created_by": e.created_by,
            }
            for e in edges
        ],
    }


@router.get("/communities")
def dashboard_communities() -> dict[str, Any]:
    """Detected communities + hub concepts (PRD Appendix A.4)."""
    communities = detect_communities(
        min_size=COMMUNITIES_MIN_SIZE,
        hub_top_k=COMMUNITIES_HUB_TOP_K,
    )
    return {
        "min_size": COMMUNITIES_MIN_SIZE,
        "hub_top_k": COMMUNITIES_HUB_TOP_K,
        "communities": [
            {
                "index": c.index,
                "size": c.size,
                "node_ids": c.node_ids,
                "hubs": [
                    {"node_id": h.node_id, "title": h.title, "degree": h.degree}
                    for h in c.hubs
                ],
            }
            for c in communities
        ],
    }


@router.get("/agents")
def dashboard_agents() -> dict[str, Any]:
    """Latest run + 7-day cost rollup per agent."""
    now = _utcnow()
    seven_days_ago = now - timedelta(days=7)

    with Session(get_engine()) as session:
        # Latest run per agent
        latest_per_agent: dict[str, ApiUsage] = {}
        for row in session.exec(select(ApiUsage).order_by(desc(ApiUsage.created_at))).all():
            if row.agent not in latest_per_agent:
                latest_per_agent[row.agent] = row

        # 7-day cost per agent
        cost_rows = list(session.exec(
            select(ApiUsage).where(ApiUsage.created_at >= seven_days_ago)
        ).all())
        cost_by_agent: dict[str, float] = {}
        runs_by_agent: dict[str, int] = {}
        for r in cost_rows:
            cost_by_agent[r.agent] = cost_by_agent.get(r.agent, 0.0) + (r.cost_usd or 0.0)
            runs_by_agent[r.agent] = runs_by_agent.get(r.agent, 0) + 1

    agents_payload: list[dict[str, Any]] = []
    for agent in sorted(set(list(latest_per_agent.keys()) + list(cost_by_agent.keys()))):
        latest = latest_per_agent.get(agent)
        agents_payload.append({
            "agent": agent,
            "latest_run_at": (
                latest.created_at.isoformat() if latest and latest.created_at else None
            ),
            "latest_succeeded": (latest.succeeded if latest else None),
            "latest_model": (latest.model if latest else None),
            "runs_7d": runs_by_agent.get(agent, 0),
            "cost_usd_7d": round(cost_by_agent.get(agent, 0.0), 4),
        })

    return {"generated_at": now.isoformat(), "agents": agents_payload}


# ── Inbox + librarian trigger ────────────────────────────────────────────────

_FRONTMATTER_SOURCE_PREFIX = "source:"


def _parse_source_from_frontmatter(text: str) -> str:
    """Cheap source extraction from a markdown file's YAML frontmatter.

    Avoids a full YAML parse just to read one field — the inbox files are
    machine-written with a stable shape.
    """
    if not text.startswith("---"):
        return "unknown"
    for line in text.splitlines()[1:25]:
        stripped = line.strip()
        if stripped == "---":
            break
        if stripped.startswith(_FRONTMATTER_SOURCE_PREFIX):
            return stripped[len(_FRONTMATTER_SOURCE_PREFIX):].strip() or "unknown"
    return "unknown"


@router.get("/inbox")
def dashboard_inbox(
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """List pending inbox items (captures not yet processed by the Librarian).

    Items are sorted oldest first so the dashboard can show what's been waiting.
    """
    settings = get_settings()
    inbox_dir = settings.inbox_dir
    if not inbox_dir.is_dir():
        return {"total": 0, "items": []}

    files = sorted(
        (p for p in inbox_dir.glob("*.md") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
    )
    items: list[dict[str, Any]] = []
    for p in files[:limit]:
        try:
            stat = p.stat()
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        items.append({
            "filename": p.name,
            "source": _parse_source_from_frontmatter(text),
            "size_bytes": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        })
    return {"total": len(files), "items": items}


@router.post("/librarian/run", status_code=status.HTTP_200_OK)
async def dashboard_trigger_librarian() -> dict[str, Any]:
    """Synchronously run the Librarian on the current inbox.

    Returns the agent's summary + artifacts so the dashboard can show the
    result. Bypasses scheduling — the user explicitly asked for this.
    """
    from synapse.agents.librarian import librarian

    result = await librarian.run()
    return {
        "ok": result.ok,
        "summary": result.summary,
        "artifacts": result.artifacts,
        "errors": result.errors,
    }
