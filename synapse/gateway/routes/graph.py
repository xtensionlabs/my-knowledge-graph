"""`/graph/*` — read API over the knowledge graph.

In M1: search, single-node retrieval, stats, manual node creation.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from synapse.config import SEARCH_DEFAULT_LIMIT
from synapse.graph.models import NodeType
from synapse.graph.operations import (
    GraphError,
    build_networkx_graph,
    compute_centrality,
    create_node,
    get_node_with_edges,
    graph_stats,
)
from synapse.graph.search import search

router = APIRouter(prefix="/graph", tags=["graph"])


class SearchHitOut(BaseModel):
    """One search result row in the HTTP response."""

    node_id: str
    score: float
    distance: float
    centrality: float
    title: str
    type: str
    snippet: str


class SearchResponse(BaseModel):
    """Schema for `GET /graph/search`."""

    query: str
    hits: list[SearchHitOut]


@router.get("/search", response_model=SearchResponse)
async def search_graph(
    q: Annotated[str, Query(min_length=1, description="search query")],
    types: Annotated[str | None, Query(description="comma-separated node types")] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = SEARCH_DEFAULT_LIMIT,
) -> SearchResponse:
    """Semantic search ranked by similarity + graph centrality."""
    type_filter = [t.strip() for t in (types.split(",") if types else []) if t.strip()]
    centrality = compute_centrality(build_networkx_graph())
    hits = search(q, types=type_filter or None, limit=limit, centrality_lookup=centrality)
    return SearchResponse(
        query=q,
        hits=[
            SearchHitOut(
                node_id=h.node_id,
                score=round(h.score, 4),
                distance=round(h.distance, 4),
                centrality=round(h.centrality, 4),
                title=str(h.metadata.get("title", "")),
                type=str(h.metadata.get("type", "")),
                snippet=h.document[:240],
            )
            for h in hits
        ],
    )


class NodeOut(BaseModel):
    """Full node payload with edges."""

    id: str
    type: str
    title: str
    content: str
    tags: list[str]
    source_ids: list[str]
    needs_review: bool
    startup_relevance_score: float
    out_edges: list[dict[str, Any]]
    in_edges: list[dict[str, Any]]


@router.get("/nodes/{node_id}", response_model=NodeOut)
async def get_node_endpoint(node_id: str) -> NodeOut:
    """Return a node + its edges."""
    bundle = get_node_with_edges(node_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail=f"node {node_id} not found")
    n = bundle.node
    return NodeOut(
        id=n.id,
        type=n.type.value if isinstance(n.type, NodeType) else str(n.type),
        title=n.title,
        content=n.content,
        tags=json.loads(n.tags or "[]"),
        source_ids=json.loads(n.source_ids or "[]"),
        needs_review=n.needs_review,
        startup_relevance_score=n.startup_relevance_score,
        out_edges=[
            {"target": e.target_node_id, "relation": e.relation_type, "weight": e.weight}
            for e in bundle.out_edges
        ],
        in_edges=[
            {"source": e.source_node_id, "relation": e.relation_type, "weight": e.weight}
            for e in bundle.in_edges
        ],
    )


class StatsResponse(BaseModel):
    """Schema for `GET /graph/stats`."""

    node_count: int
    edge_count: int
    nodes_by_type: dict[str, int]
    needs_review: int
    orphan_count: int


@router.get("/stats", response_model=StatsResponse)
async def stats_endpoint() -> StatsResponse:
    """Tabular health of the graph."""
    return StatsResponse(**graph_stats())


class CreateNodePayload(BaseModel):
    """Schema for `POST /graph/nodes` (manual creation)."""

    type: str
    title: str = Field(min_length=1)
    content: str = ""
    tags: list[str] = Field(default_factory=list)


@router.post("/nodes", response_model=NodeOut, status_code=status.HTTP_201_CREATED)
async def create_node_endpoint(payload: CreateNodePayload) -> NodeOut:
    """Create a node manually (no edges — use /graph/edges for those, M2+)."""
    try:
        node = create_node(
            type=payload.type,
            title=payload.title,
            content=payload.content,
            tags=payload.tags,
        )
    except GraphError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return NodeOut(
        id=node.id,
        type=node.type.value if isinstance(node.type, NodeType) else str(node.type),
        title=node.title,
        content=node.content,
        tags=json.loads(node.tags or "[]"),
        source_ids=json.loads(node.source_ids or "[]"),
        needs_review=node.needs_review,
        startup_relevance_score=node.startup_relevance_score,
        out_edges=[],
        in_edges=[],
    )
