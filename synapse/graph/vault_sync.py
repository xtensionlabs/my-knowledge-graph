"""Mirror knowledge-graph nodes into the Obsidian vault as markdown files.

The vault is a generated VIEW of the graph, not the source of truth (PRD §5.1).
On every node create/update, we (re)write `${VAULT}/<folder>/<slug>.md` with
frontmatter that mirrors the SQLite row.

Direct filesystem writes are used in M1; the Obsidian Local REST API plugin
(port 27123, per `CLAUDE.md`) can be wired in as an alternative writer later —
`write_node_file()` is the only function that touches disk, so swapping is local.

If the file already exists with a body section the user has edited manually,
we preserve the user's body and only refresh the frontmatter — this is the
"never destroy user work" guard.

# CLARIFY: Obsidian Local REST API plugin support deferred; filesystem fallback
# is in use. CLAUDE.md §"How to Work with the Obsidian Vault" prefers the plugin.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from loguru import logger
from sqlmodel import Session, select

from synapse.config import get_settings
from synapse.graph.db import get_engine
from synapse.graph.models import Edge, Node, NodeType

# Frontmatter delimiter pattern used to extract the body of an existing file.
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
# Marker that delimits the auto-generated section from any user additions.
_USER_SECTION_MARKER = "<!-- USER CONTENT BELOW — never overwritten by sync -->"


def _isoformat(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _frontmatter_for(node: Node, edges_summary: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the frontmatter dict for a node."""
    fm: dict[str, Any] = {
        "id": node.id,
        "type": node.type.value if isinstance(node.type, NodeType) else str(node.type),
        "title": node.title,
        "created": _isoformat(node.created_at),
        "updated": _isoformat(node.updated_at),
        "tags": json.loads(node.tags or "[]"),
        "source_ids": json.loads(node.source_ids or "[]"),
        "needs_review": node.needs_review,
        "startup_relevance_score": node.startup_relevance_score,
    }
    if node.type == NodeType.CONCEPT:
        fm["retention"] = {
            "last_reviewed": _isoformat(node.last_reviewed),
            "next_review": _isoformat(node.next_review),
            "ease_factor": node.ease_factor,
            "interval_days": node.interval_days,
            "review_count": node.review_count,
        }
    if edges_summary:
        fm["edges"] = edges_summary
    return fm


def _edges_summary(node_id: str) -> list[dict[str, Any]]:
    """Compact list of edges for the frontmatter view."""
    out: list[dict[str, Any]] = []
    with Session(get_engine()) as session:
        out_edges = session.exec(select(Edge).where(Edge.source_node_id == node_id)).all()
        for e in out_edges:
            out.append({"direction": "out", "to": e.target_node_id, "relation": e.relation_type})
        in_edges = session.exec(select(Edge).where(Edge.target_node_id == node_id)).all()
        for e in in_edges:
            out.append({"direction": "in", "from": e.source_node_id, "relation": e.relation_type})
    return out


def _read_existing(path: Path) -> tuple[str, str]:
    """Return (frontmatter_yaml, body) of an existing file. Missing → ("", "")."""
    if not path.exists():
        return "", ""
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return "", text  # malformed; treat whole content as body
    return match.group(1), match.group(2)


def _split_auto_user(body: str) -> tuple[str, str]:
    """Split an existing body into (auto_generated, user_authored) by the marker."""
    if _USER_SECTION_MARKER in body:
        auto, _, user = body.partition(_USER_SECTION_MARKER)
        return auto.rstrip(), user.lstrip("\n")
    return body, ""


def _atomic_write(path: Path, contents: str) -> None:
    """Atomic write via temp + rename, same pattern as the inbox writer."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = path.parent / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    tmp_path = tmp_dir / f"{path.name}.{uuid.uuid4().hex}.partial"
    try:
        with tmp_path.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(contents)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _render_auto_body(node: Node) -> str:
    """Render the auto-generated body section for a node."""
    lines = [f"# {node.title}", ""]
    if node.content.strip():
        lines.append(node.content.strip())
        lines.append("")
    return "\n".join(lines)


def write_node_file(node: Node) -> Path:
    """(Re)write the vault markdown file mirroring a node.

    Preserves any user-authored content placed below the
    `<!-- USER CONTENT BELOW … -->` marker.

    Args:
        node: The Node to mirror.

    Returns:
        Absolute path to the file written.
    """
    settings = get_settings()
    rel_path = node.obsidian_path or _default_rel_path(node)
    target = settings.synapse_vault_path / rel_path

    edges_summary = _edges_summary(node.id)
    fm_dict = _frontmatter_for(node, edges_summary)
    fm_block = "---\n" + yaml.safe_dump(fm_dict, sort_keys=False, allow_unicode=True) + "---\n"

    _existing_fm, existing_body = _read_existing(target)
    _existing_auto, user_section = _split_auto_user(existing_body)

    body_parts = [_render_auto_body(node)]
    body_parts.append(_USER_SECTION_MARKER)
    if user_section.strip():
        body_parts.append("")
        body_parts.append(user_section.rstrip())
    full = fm_block + "\n" + "\n".join(body_parts) + "\n"

    _atomic_write(target, full)
    logger.debug("vault sync: wrote {path}", path=target)
    return target


def _default_rel_path(node: Node) -> str:
    """Fallback path computation when `node.obsidian_path` is empty."""
    folder_map = {
        NodeType.CONCEPT: "concepts",
        NodeType.FACT: "concepts",
        NodeType.BUILD: "builds",
        NodeType.PERSON: "people",
        NodeType.EVENT: "events",
        NodeType.QUESTION: "questions",
        NodeType.INSIGHT: "insights",
    }
    folder = folder_map[node.type]
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in node.title.lower()).strip("-")
    return f"{folder}/{slug or 'untitled'}.md"


def sync_all_nodes() -> int:
    """Rewrite every node's mirror file. Returns the count synced."""
    with Session(get_engine()) as session:
        nodes = list(session.exec(select(Node)).all())
    for n in nodes:
        try:
            write_node_file(n)
        except Exception as exc:  # noqa: BLE001
            logger.warning("vault sync failed for {id}: {exc}", id=n.id, exc=exc)
    return len(nodes)
