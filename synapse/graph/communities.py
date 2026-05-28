"""Community detection — PRD Appendix A.4.

Real brain networks have high local clustering (expertise pockets) and short
global path lengths (cross-domain analogies). NetworkX's Louvain algorithm
finds the clusters; within each cluster we surface the highest-degree
nodes as "hub concepts" — the load-bearing ideas the user's graph orbits.

This is read-only: it never mutates the graph. The Dashboard (M5b) consumes
the same primitives to render the community map.
"""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx
from loguru import logger

from synapse.config import COMMUNITIES_HUB_TOP_K, COMMUNITIES_MIN_SIZE
from synapse.graph.operations import build_networkx_graph


@dataclass
class Hub:
    """One hub concept inside a community."""

    node_id: str
    title: str
    degree: int


@dataclass
class Community:
    """One detected community."""

    index: int
    size: int
    node_ids: list[str]
    hubs: list[Hub]


def detect_communities(
    *,
    min_size: int = COMMUNITIES_MIN_SIZE,
    hub_top_k: int = COMMUNITIES_HUB_TOP_K,
) -> list[Community]:
    """Run Louvain community detection and surface hub concepts per community.

    Args:
        min_size:  Communities smaller than this are dropped (noise floor).
        hub_top_k: How many highest-degree nodes to flag as hubs per community.

    Returns:
        List of Community objects, largest first.
    """
    digraph = build_networkx_graph()
    if digraph.number_of_nodes() == 0:
        return []

    # Louvain wants an undirected graph; collapse direction by summing weights.
    undirected = nx.Graph()
    for u, v, data in digraph.edges(data=True):
        w = float(data.get("weight", 1.0))
        if undirected.has_edge(u, v):
            undirected[u][v]["weight"] += w
        else:
            undirected.add_edge(u, v, weight=w)
    # Add isolated nodes so they survive into the membership map.
    for n in digraph.nodes():
        if n not in undirected:
            undirected.add_node(n)

    try:
        communities = nx.community.louvain_communities(undirected, weight="weight", seed=42)
    except Exception as exc:  # noqa: BLE001
        logger.warning("louvain failed; falling back to greedy modularity: {exc}", exc=exc)
        communities = nx.community.greedy_modularity_communities(undirected, weight="weight")

    # Build node_id -> title lookup once.
    titles: dict[str, str] = {
        n: digraph.nodes[n].get("title", "")
        for n in digraph.nodes()
    }
    # Degree on the undirected (collapsed) graph is the meaningful score for hubs.
    degrees: dict[str, int] = dict(undirected.degree())

    out: list[Community] = []
    for idx, raw in enumerate(communities):
        node_ids = sorted(raw)
        if len(node_ids) < min_size:
            continue
        ranked = sorted(node_ids, key=lambda n: degrees.get(n, 0), reverse=True)
        hubs = [
            Hub(node_id=n, title=titles.get(n, ""), degree=degrees.get(n, 0))
            for n in ranked[:hub_top_k]
        ]
        out.append(Community(index=idx, size=len(node_ids), node_ids=node_ids, hubs=hubs))

    # Sort by size descending so the dashboard renders the most important first.
    out.sort(key=lambda c: c.size, reverse=True)
    # Reassign sequential indices for cleaner display.
    for new_idx, c in enumerate(out):
        c.index = new_idx
    return out
