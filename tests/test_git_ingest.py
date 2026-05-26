"""Tests for synapse/agents/git_ingest.py + Librarian git routing.

M3 success gate: 5 commits → 5 BUILD nodes, one confirmed INSIGHT node.
The gate tests live at the bottom of this file.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from synapse.agents.git_ingest import GitIngestAgent, git_ingest_agent
from synapse.agents.librarian import (
    InboxItem,
    PendingInsight,
    confirm_insight,
    parse_pending_insights,
)
from synapse.capture.inbox import write_to_inbox
from synapse.config import LIBRARIAN_PENDING_INSIGHTS_FILE
from synapse.graph import operations
from synapse.graph.models import NodeType
from synapse.manifest import SynapseManifest, write_manifest


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_git_item(
    *,
    repo: str = "my-repo",
    branch: str = "main",
    commit_hash: str = "abc1234abcd",
    message: str = "feat: add something",
    repo_path: str | None = None,
) -> InboxItem:
    """Build a synthetic git InboxItem (bypasses the gateway)."""
    from synapse.capture.inbox import write_to_inbox
    from synapse.config import get_settings
    import re

    body = (
        f"**Repo:** {repo}\n"
        f"**Branch:** {branch}\n"
        f"**Commit:** `{commit_hash}`\n\n"
        f"{message}\n"
    )
    extra: dict[str, object] = {
        "repo": repo,
        "branch": branch,
        "commit_hash": commit_hash,
        "title": f"{repo}@{commit_hash[:7]}",
    }
    if repo_path is not None:
        extra["repo_path"] = repo_path

    path = write_to_inbox(source="git", content=body, extra=extra)

    import yaml
    _FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    assert m, f"no frontmatter in {path}"
    fm = yaml.safe_load(m.group(1)) or {}
    return InboxItem(path=path, frontmatter=fm, body=m.group(2).strip())


@pytest.fixture(autouse=True)
def _stub_embeddings():  # type: ignore[no-untyped-def]
    with patch.object(operations, "_embed_and_upsert", lambda node: None):
        yield


# ── GitIngestAgent unit tests ─────────────────────────────────────────────────


def test_git_ingest_creates_build_node(isolated_vault: Path) -> None:
    item = _make_git_item(repo="xtension-signal")
    result = git_ingest_agent.process_item(item.frontmatter, item.body, item.capture_id)
    assert result.ok
    assert result.repo == "xtension-signal"
    node = operations.find_node_by_title("xtension-signal")
    assert node is not None
    assert node.type == NodeType.BUILD


def test_git_ingest_deduplicates_same_repo(isolated_vault: Path) -> None:
    """Two commits to the same repo → one BUILD node (updated)."""
    item1 = _make_git_item(repo="my-app", commit_hash="aaa111")
    item2 = _make_git_item(repo="my-app", commit_hash="bbb222")
    git_ingest_agent.process_item(item1.frontmatter, item1.body, item1.capture_id)
    git_ingest_agent.process_item(item2.frontmatter, item2.body, item2.capture_id)

    from sqlmodel import Session, select
    from synapse.graph.db import get_engine
    from synapse.graph.models import Node

    with Session(get_engine()) as s:
        nodes = s.exec(select(Node).where(Node.type == NodeType.BUILD)).all()
    build_titles = [n.title for n in nodes]
    assert build_titles.count("my-app") == 1, f"Expected 1 BUILD node, got: {build_titles}"


def test_git_ingest_updates_content_on_second_commit(isolated_vault: Path) -> None:
    item1 = _make_git_item(repo="evolving-repo", commit_hash="first00")
    item2 = _make_git_item(repo="evolving-repo", commit_hash="second0", message="fix: patch bug")
    git_ingest_agent.process_item(item1.frontmatter, item1.body, item1.capture_id)
    git_ingest_agent.process_item(item2.frontmatter, item2.body, item2.capture_id)

    node = operations.find_node_by_title("evolving-repo")
    assert node is not None
    assert "patch bug" in node.content


def test_git_ingest_reads_manifest_concepts(isolated_vault: Path, tmp_path: Path) -> None:
    """If synapse.json is present, BUILD↔CONCEPT edges are created."""
    # First create a CONCEPT in the graph.
    operations.create_node(type=NodeType.CONCEPT, title="BFS")

    # Write a manifest into a fake repo_path.
    repo_path = tmp_path / "fake-repo"
    repo_path.mkdir()
    manifest = SynapseManifest(module="fake-repo", cs_concepts=["BFS"])
    write_manifest(repo_path, manifest)

    item = _make_git_item(repo="fake-repo", repo_path=str(repo_path))
    result = git_ingest_agent.process_item(item.frontmatter, item.body, item.capture_id)

    assert result.ok
    assert result.edges_created == 1

    from sqlmodel import Session, select
    from synapse.graph.db import get_engine
    from synapse.graph.models import Edge

    build_node = operations.find_node_by_title("fake-repo")
    assert build_node is not None
    with Session(get_engine()) as s:
        edges = s.exec(
            select(Edge).where(Edge.target_node_id == build_node.id)
        ).all()
    assert any(e.relation_type == "applies_to" for e in edges)


def test_git_ingest_manifest_updates_last_commit(isolated_vault: Path, tmp_path: Path) -> None:
    """After ingest, synapse.json.last_commit should match the commit hash."""
    from synapse.manifest import read_manifest

    repo_path = tmp_path / "update-test"
    repo_path.mkdir()
    write_manifest(repo_path, SynapseManifest(module="update-test"))

    item = _make_git_item(repo="update-test", commit_hash="cafebabe01", repo_path=str(repo_path))
    git_ingest_agent.process_item(item.frontmatter, item.body, item.capture_id)

    m = read_manifest(repo_path)
    assert m is not None
    assert m.last_commit == "cafebabe01"


def test_git_ingest_skips_missing_concept(isolated_vault: Path, tmp_path: Path) -> None:
    """cs_concepts entry for a node that doesn't exist → no crash, 0 edges."""
    repo_path = tmp_path / "ghost-concepts"
    repo_path.mkdir()
    write_manifest(repo_path, SynapseManifest(module="ghost-concepts", cs_concepts=["NonExistentConcept"]))

    item = _make_git_item(repo="ghost-concepts", repo_path=str(repo_path))
    result = git_ingest_agent.process_item(item.frontmatter, item.body, item.capture_id)

    assert result.ok
    assert result.edges_created == 0


# ── Librarian routing tests ───────────────────────────────────────────────────


def test_librarian_routes_git_items_without_claude(isolated_vault: Path) -> None:
    """Librarian must process `source=git` items without calling Claude."""
    from synapse.agents.librarian import librarian
    import asyncio

    item = _make_git_item(repo="librarian-test-repo")
    # No mock of ClaudeClient needed — git items must not reach it.
    call_count = {"n": 0}

    original_structured = librarian._client.structured

    async def _spy(*args, **kwargs):  # type: ignore[no-untyped-def]
        call_count["n"] += 1
        return await original_structured(*args, **kwargs)

    librarian._client.structured = _spy  # type: ignore[assignment]
    try:
        result = asyncio.run(librarian.run(max_items=10))
    finally:
        librarian._client.structured = original_structured  # type: ignore[assignment]

    assert result.ok
    assert call_count["n"] == 0, "Claude was called for a git item — should be deterministic"
    node = operations.find_node_by_title("librarian-test-repo")
    assert node is not None and node.type == NodeType.BUILD


def test_librarian_archives_git_item(isolated_vault: Path) -> None:
    """After Librarian runs, the git inbox item must be in archive/."""
    from synapse.agents.librarian import librarian
    import asyncio

    item = _make_git_item(repo="archive-test")
    inbox_path = item.path

    asyncio.run(librarian.run(max_items=10))

    assert not inbox_path.exists(), "git item was not removed from inbox"
    from synapse.config import get_settings
    archive_copy = get_settings().archive_dir / inbox_path.name
    assert archive_copy.exists(), "git item was not moved to archive"


# ── M3 success gate ───────────────────────────────────────────────────────────


def test_m3_gate_five_commits_create_five_build_nodes(isolated_vault: Path) -> None:
    """M3 gate: 5 commits to 5 different repos → 5 BUILD nodes."""
    repos = [
        "xtension-signal",
        "xtension-dashboard",
        "xtension-api",
        "xtension-mobile",
        "xtension-infra",
    ]
    for repo in repos:
        item = _make_git_item(repo=repo, commit_hash=f"hash-{repo}")
        result = git_ingest_agent.process_item(item.frontmatter, item.body, item.capture_id)
        assert result.ok, f"failed for {repo}: {result.error}"

    from sqlmodel import Session, select
    from synapse.graph.db import get_engine
    from synapse.graph.models import Node

    with Session(get_engine()) as s:
        build_nodes = s.exec(select(Node).where(Node.type == NodeType.BUILD)).all()
    build_titles = {n.title for n in build_nodes}
    for r in repos:
        assert r in build_titles, f"BUILD node missing for {r}"


def test_m3_gate_confirmed_insight_creates_insight_node(isolated_vault: Path) -> None:
    """M3 gate: one user-confirmed INSIGHT candidate becomes an INSIGHT node."""
    from synapse.config import get_settings

    # Seed a pending_insights.md entry.
    file = get_settings().synapse_vault_path / LIBRARIAN_PENDING_INSIGHTS_FILE
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(
        "\n## From inbox_test.md — 2026-05-26T10:00:00+00:00\n\n"
        "- BFS graph traversal mirrors the Signal event fan-out pattern\n"
        "  - Nodes: BFS, xtension-signal\n",
        encoding="utf-8",
    )

    entries = parse_pending_insights(file)
    assert len(entries) >= 1

    # Create the referenced nodes so edge wiring works.
    operations.create_node(type=NodeType.CONCEPT, title="BFS")
    operations.create_node(type=NodeType.BUILD, title="xtension-signal")

    node_id = confirm_insight(entries[0], file)
    assert node_id

    node = operations.get_node(node_id)
    assert node is not None
    assert node.type == NodeType.INSIGHT
    assert "BFS" in node.title or "BFS" in node.content


# ── INSIGHT promotion unit tests ──────────────────────────────────────────────


def test_parse_pending_insights_empty_file(isolated_vault: Path) -> None:
    from synapse.config import get_settings

    file = get_settings().synapse_vault_path / LIBRARIAN_PENDING_INSIGHTS_FILE
    assert parse_pending_insights(file) == []


def test_parse_pending_insights_returns_entries(isolated_vault: Path) -> None:
    from synapse.config import get_settings

    file = get_settings().synapse_vault_path / LIBRARIAN_PENDING_INSIGHTS_FILE
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(
        "\n## From inbox_a.md — 2026-01-01T00:00:00+00:00\n\n"
        "- First insight description\n"
        "  - Nodes: NodeA, NodeB\n"
        "- Second insight description\n"
        "  - Nodes: NodeC\n",
        encoding="utf-8",
    )
    entries = parse_pending_insights(file)
    assert len(entries) == 2
    assert entries[0].description == "First insight description"
    assert "NodeA" in entries[0].node_titles
    assert entries[1].description == "Second insight description"
    assert entries[1].node_titles == ["NodeC"]


def test_confirm_insight_removes_entry_from_file(isolated_vault: Path) -> None:
    from synapse.config import get_settings

    file = get_settings().synapse_vault_path / LIBRARIAN_PENDING_INSIGHTS_FILE
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(
        "\n## From inbox_x.md — 2026-01-01T00:00:00+00:00\n\n"
        "- Keep this one\n"
        "  - Nodes: NodeK\n"
        "- Remove this one\n"
        "  - Nodes: NodeR\n",
        encoding="utf-8",
    )
    entries = parse_pending_insights(file)
    to_remove = next(e for e in entries if e.description == "Remove this one")
    confirm_insight(to_remove, file)

    remaining = parse_pending_insights(file)
    descriptions = [e.description for e in remaining]
    assert "Remove this one" not in descriptions
    assert "Keep this one" in descriptions


def test_confirm_insight_creates_edges_to_existing_nodes(isolated_vault: Path) -> None:
    from synapse.config import get_settings
    from sqlmodel import Session, select
    from synapse.graph.db import get_engine
    from synapse.graph.models import Edge

    operations.create_node(type=NodeType.CONCEPT, title="EdgeConcept")

    entry = PendingInsight(
        index=1,
        description="Test edge wiring",
        node_titles=["EdgeConcept"],
        raw_block="- Test edge wiring\n  - Nodes: EdgeConcept",
    )
    file = get_settings().synapse_vault_path / LIBRARIAN_PENDING_INSIGHTS_FILE
    node_id = confirm_insight(entry, file)

    with Session(get_engine()) as s:
        edges = s.exec(
            select(Edge).where(Edge.source_node_id == node_id)
        ).all()
    assert any(e.relation_type == "derived_from" for e in edges)
