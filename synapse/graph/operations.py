"""Knowledge-graph CRUD + traversal.

This module is the single funnel for node/edge writes. Every write keeps the
SQLite source-of-truth and the ChromaDB vector store in sync. A NetworkX
projection is built on demand for centrality and traversal queries — never
persisted, always reconstructible from SQLite.

Per `CLAUDE.md` §"Non-Negotiable Rules":
    - Never delete. The deletion API does not exist in this module.
    - All graph writes are explicit; never silently inferred from LLM output
      (the Librarian validates the payload first and then calls these functions).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import networkx as nx
from loguru import logger
from sqlmodel import Session, select

from synapse.graph.db import get_engine
from synapse.graph.embeddings import embed_text
from synapse.graph.models import Edge, Node, NodeType, RelationType
from synapse.graph.search import delete_node_embedding, upsert_node_embedding
from synapse.utils.time import utcnow as _utcnow


# ── Exceptions ───────────────────────────────────────────────────────────────


class GraphError(Exception):
    """Base for graph layer errors."""


class DuplicateNodeError(GraphError):
    """Raised when a strict create encounters an existing node by title+type."""


class UnknownRelationError(GraphError):
    """Raised when an edge relation_type is not in `RelationType`."""


# ── DTOs ─────────────────────────────────────────────────────────────────────


@dataclass
class NodeSummary:
    """Light projection for Librarian dedup context."""

    id: str
    type: str
    title: str
    tag_count: int


@dataclass
class NodeWithEdges:
    """A node plus its incoming and outgoing edges."""

    node: Node
    out_edges: list[Edge]
    in_edges: list[Edge]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _normalize_title(title: str) -> str:
    """Lowercase + collapse whitespace, for case-insensitive dedup lookup."""
    return " ".join(title.lower().split())


def _coerce_node_type(value: Any) -> NodeType:
    """Accept either a NodeType or a string; reject anything else."""
    if isinstance(value, NodeType):
        return value
    if isinstance(value, str):
        try:
            return NodeType(value.upper())
        except ValueError as exc:
            raise GraphError(f"unknown node type: {value!r}") from exc
    raise GraphError(f"node type must be str or NodeType, got {type(value).__name__}")


def _validate_relation(relation: str) -> str:
    """Ensure `relation` is one of `RelationType`."""
    try:
        return RelationType(relation).value
    except ValueError as exc:
        raise UnknownRelationError(
            f"unknown relation_type {relation!r}; valid: "
            f"{[r.value for r in RelationType]}"
        ) from exc


def _slugify(title: str, max_len: int = 80) -> str:
    """Filename-safe slug derived from a title."""
    clean = "".join(c if c.isalnum() or c in "-_ " else "-" for c in title)
    clean = clean.strip().replace(" ", "-").lower()
    while "--" in clean:
        clean = clean.replace("--", "-")
    return clean[:max_len].strip("-") or "untitled"


def _vault_path_for(node_type: NodeType, title: str) -> str:
    """Compute the vault-relative path where this node's mirror file lives."""
    folder_map = {
        NodeType.CONCEPT: "concepts",
        NodeType.FACT: "concepts",  # facts live alongside concepts they support
        NodeType.BUILD: "builds",
        NodeType.PERSON: "people",
        NodeType.EVENT: "events",
        NodeType.QUESTION: "questions",
        NodeType.INSIGHT: "insights",
    }
    folder = folder_map[node_type]
    return f"{folder}/{_slugify(title)}.md"


def _embedding_text(node: Node) -> str:
    """Compose the text we feed into the embedder for a node."""
    return f"{node.title}\n\n{node.content}".strip()


def _embed_and_upsert(node: Node) -> None:
    """Push the node's vector + metadata into ChromaDB. Best-effort."""
    try:
        text = _embedding_text(node)
        metadata = {
            "type": node.type.value if isinstance(node.type, NodeType) else str(node.type),
            "title": node.title,
            "tags": json.loads(node.tags or "[]"),
            "needs_review": node.needs_review,
        }
        upsert_node_embedding(node_id=node.id, text=text, metadata=metadata)
    except Exception as exc:  # noqa: BLE001 — ChromaDB shouldn't break graph writes
        logger.warning(
            "embedding upsert failed for node {id}: {exc}", id=node.id, exc=exc
        )


# ── Node CRUD ────────────────────────────────────────────────────────────────


def create_node(
    *,
    type: NodeType | str,
    title: str,
    content: str = "",
    source_ids: Iterable[str] | None = None,
    tags: Iterable[str] | None = None,
    needs_review: bool = False,
    startup_relevance_score: float = 0.0,
    dedup: bool = True,
) -> Node:
    """Create a node and sync its embedding to ChromaDB.

    Args:
        type: NodeType enum or its string name (case-insensitive).
        title: Human-readable title; used for dedup if `dedup=True`.
        content: Markdown body.
        source_ids: Capture ids that gave rise to this node (frontmatter `id`s).
        tags: Free-form tags.
        needs_review: Set by Librarian when confidence < threshold.
        startup_relevance_score: 0..1; populated by Librarian for BUILD/CONCEPT nodes.
        dedup: If True (default), return the existing node when a case-insensitive
            title match exists for the same type.

    Returns:
        The created (or pre-existing) Node.

    Raises:
        DuplicateNodeError: If `dedup=False` and a duplicate exists.
        GraphError: For invalid node type or other validation failures.
    """
    node_type = _coerce_node_type(type)
    title = title.strip()
    if not title:
        raise GraphError("title cannot be empty")

    with Session(get_engine()) as session:
        existing = _find_node_by_title_no_session(
            session, title=title, type_=node_type
        )
        if existing is not None:
            if dedup:
                logger.debug("create_node: returning existing {id}", id=existing.id)
                return existing
            raise DuplicateNodeError(
                f"node with title={title!r} and type={node_type} already exists"
            )

        node = Node(
            id=str(uuid.uuid4()),
            type=node_type,
            title=title,
            content=content,
            source_ids=json.dumps(list(source_ids or [])),
            tags=json.dumps(list(tags or [])),
            needs_review=needs_review,
            startup_relevance_score=startup_relevance_score,
            obsidian_path=_vault_path_for(node_type, title),
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        session.add(node)
        session.commit()
        session.refresh(node)

    _embed_and_upsert(node)
    logger.info("created node {type} {title!r} ({id})",
                type=node_type.value, title=title, id=node.id)
    return node


def update_node(
    node_id: str,
    *,
    content_addition: str | None = None,
    new_source_ids: Iterable[str] | None = None,
    new_tags: Iterable[str] | None = None,
    needs_review: bool | None = None,
    startup_relevance_score: float | None = None,
) -> Node:
    """Update a node additively (never overwrite existing content).

    Args:
        node_id: PK of the node to update.
        content_addition: Markdown to append (separated by a blank line + `---`).
        new_source_ids: Capture ids to merge into `source_ids`.
        new_tags: Tags to merge.
        needs_review: Replace the flag if provided.
        startup_relevance_score: Replace the score if provided.

    Returns:
        The updated Node.
    """
    with Session(get_engine()) as session:
        node = session.get(Node, node_id)
        if node is None:
            raise GraphError(f"node {node_id!r} not found")

        if content_addition and content_addition.strip():
            sep = "\n\n---\n\n" if node.content.strip() else ""
            node.content = node.content + sep + content_addition.strip()

        if new_source_ids:
            existing_sources = set(json.loads(node.source_ids or "[]"))
            existing_sources.update(new_source_ids)
            node.source_ids = json.dumps(sorted(existing_sources))

        if new_tags:
            existing_tags = set(json.loads(node.tags or "[]"))
            existing_tags.update(new_tags)
            node.tags = json.dumps(sorted(existing_tags))

        if needs_review is not None:
            node.needs_review = needs_review
        if startup_relevance_score is not None:
            node.startup_relevance_score = startup_relevance_score

        node.updated_at = _utcnow()
        session.add(node)
        session.commit()
        session.refresh(node)

    _embed_and_upsert(node)
    logger.info("updated node {id}", id=node_id)
    return node


def get_node(node_id: str) -> Node | None:
    """Return a node by id, or None."""
    with Session(get_engine()) as session:
        return session.get(Node, node_id)


def get_node_with_edges(node_id: str) -> NodeWithEdges | None:
    """Return a node together with its incoming and outgoing edges."""
    with Session(get_engine()) as session:
        node = session.get(Node, node_id)
        if node is None:
            return None
        out_edges = list(session.exec(select(Edge).where(Edge.source_node_id == node_id)).all())
        in_edges = list(session.exec(select(Edge).where(Edge.target_node_id == node_id)).all())
        return NodeWithEdges(node=node, out_edges=out_edges, in_edges=in_edges)


def find_node_by_title(title: str, *, type_: NodeType | str | None = None) -> Node | None:
    """Case-insensitive lookup by title, optionally narrowed by node type."""
    with Session(get_engine()) as session:
        coerced = _coerce_node_type(type_) if type_ is not None else None
        return _find_node_by_title_no_session(session, title=title, type_=coerced)


def _find_node_by_title_no_session(
    session: Session, *, title: str, type_: NodeType | None
) -> Node | None:
    """Internal: title lookup with caller-supplied session."""
    norm = _normalize_title(title)
    stmt = select(Node)
    if type_ is not None:
        stmt = stmt.where(Node.type == type_)
    candidates = session.exec(stmt).all()
    for n in candidates:
        if _normalize_title(n.title) == norm:
            return n
    return None


def list_nodes_summary(
    *, types: Iterable[NodeType | str] | None = None, limit: int | None = None
) -> list[NodeSummary]:
    """Return lightweight summaries of all nodes — used as Librarian dedup context.

    Args:
        types: Optional filter by node type(s).
        limit: Optional cap on the number returned.
    """
    with Session(get_engine()) as session:
        stmt = select(Node)
        if types:
            coerced = [_coerce_node_type(t) for t in types]
            stmt = stmt.where(Node.type.in_(coerced))  # type: ignore[attr-defined]
        rows = session.exec(stmt).all()
        if limit is not None:
            rows = rows[:limit]
        return [
            NodeSummary(
                id=r.id,
                type=r.type.value if isinstance(r.type, NodeType) else str(r.type),
                title=r.title,
                tag_count=len(json.loads(r.tags or "[]")),
            )
            for r in rows
        ]


def list_nodes(
    *, types: Iterable[NodeType | str] | None = None, limit: int | None = None
) -> list[Node]:
    """Return full Node objects (use sparingly for large graphs)."""
    with Session(get_engine()) as session:
        stmt = select(Node)
        if types:
            coerced = [_coerce_node_type(t) for t in types]
            stmt = stmt.where(Node.type.in_(coerced))  # type: ignore[attr-defined]
        rows = list(session.exec(stmt).all())
        if limit is not None:
            rows = rows[:limit]
        return rows


# ── Edge CRUD ────────────────────────────────────────────────────────────────


def create_edge(
    *,
    source_node_id: str,
    target_node_id: str,
    relation_type: str,
    weight: float = 1.0,
    created_by: str = "user",
    note: str | None = None,
    dedup: bool = True,
) -> Edge:
    """Create an edge between two existing nodes.

    Args:
        source_node_id: PK of the source node.
        target_node_id: PK of the target node.
        relation_type: Must be one of `RelationType`.
        weight: Edge strength (0..1+).
        created_by: `librarian` | `synthesizer` | `user`.
        note: Why this edge exists (Librarian usually populates this).
        dedup: If True (default), return existing edge with the same triple.

    Returns:
        The created (or existing) Edge.
    """
    rel = _validate_relation(relation_type)
    if source_node_id == target_node_id:
        raise GraphError("self-loops are not permitted on Synapse edges")

    with Session(get_engine()) as session:
        # Confirm both endpoints exist (FK constraint also enforces this).
        if session.get(Node, source_node_id) is None:
            raise GraphError(f"source node {source_node_id!r} not found")
        if session.get(Node, target_node_id) is None:
            raise GraphError(f"target node {target_node_id!r} not found")

        if dedup:
            existing = session.exec(
                select(Edge)
                .where(Edge.source_node_id == source_node_id)
                .where(Edge.target_node_id == target_node_id)
                .where(Edge.relation_type == rel)
            ).first()
            if existing is not None:
                return existing

        edge = Edge(
            id=str(uuid.uuid4()),
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            relation_type=rel,
            weight=weight,
            created_by=created_by,
            note=note,
            created_at=_utcnow(),
        )
        session.add(edge)
        session.commit()
        session.refresh(edge)
        return edge


def list_edges() -> list[Edge]:
    """Return all edges (small graph; full scan is fine at personal scale)."""
    with Session(get_engine()) as session:
        return list(session.exec(select(Edge)).all())


# ── Graph projection (NetworkX) ──────────────────────────────────────────────


def build_networkx_graph() -> nx.DiGraph:
    """Project the SQLite graph into NetworkX. Always reconstructible; never persisted."""
    g: nx.DiGraph = nx.DiGraph()
    with Session(get_engine()) as session:
        for node in session.exec(select(Node)).all():
            g.add_node(
                node.id,
                title=node.title,
                type=node.type.value if isinstance(node.type, NodeType) else str(node.type),
            )
        for edge in session.exec(select(Edge)).all():
            g.add_edge(
                edge.source_node_id,
                edge.target_node_id,
                relation=edge.relation_type,
                weight=edge.weight,
            )
    return g


def compute_centrality(graph: nx.DiGraph | None = None) -> dict[str, float]:
    """Compute degree-centrality for every node, normalized to [0, 1].

    Args:
        graph: Optional precomputed NetworkX graph. If None, builds one now.

    Returns:
        Mapping `node_id -> centrality`. Empty graph yields empty dict.
    """
    g = graph if graph is not None else build_networkx_graph()
    if len(g) == 0:
        return {}
    raw = nx.degree_centrality(g)
    return raw  # nx already returns values in [0, 1]


def find_orphans() -> list[Node]:
    """Return nodes with zero incoming AND zero outgoing edges.

    The M1 success gate requires zero orphans across the post-Librarian graph.
    """
    g = build_networkx_graph()
    orphan_ids = [n for n in g.nodes if g.degree(n) == 0]
    if not orphan_ids:
        return []
    with Session(get_engine()) as session:
        return list(session.exec(select(Node).where(Node.id.in_(orphan_ids))).all())  # type: ignore[attr-defined]


# ── Stats ────────────────────────────────────────────────────────────────────


def graph_stats() -> dict[str, Any]:
    """High-level snapshot of the graph for `synapse graph stats`."""
    with Session(get_engine()) as session:
        nodes = list(session.exec(select(Node)).all())
        edges = list(session.exec(select(Edge)).all())

    by_type: dict[str, int] = {}
    for n in nodes:
        key = n.type.value if isinstance(n.type, NodeType) else str(n.type)
        by_type[key] = by_type.get(key, 0) + 1

    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes_by_type": by_type,
        "needs_review": sum(1 for n in nodes if n.needs_review),
        "orphan_count": len(find_orphans()),
    }
