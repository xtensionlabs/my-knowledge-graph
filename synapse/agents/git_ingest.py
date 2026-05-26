"""GitIngestAgent — deterministic git inbox → BUILD node processor.

Called by the Librarian whenever it encounters an inbox item with
`source: git`.  This agent does NOT call Claude — git commits carry
structured metadata (repo, branch, commit_hash, files_changed, etc.)
that can be applied to the graph without LLM reasoning.

Flow per inbox item
-------------------
1. Parse frontmatter: repo, branch, commit_hash, message, repo_path.
2. Find or create a BUILD node whose title matches `repo`.
3. Append commit info to the BUILD node's content.
4. If `repo_path` is present and contains a `synapse.json`, read it and:
   a. Create BUILD→CONCEPT `applies_to` edges for each `cs_concepts` entry.
   b. Write back `last_commit` + `synapse_node_id` to the manifest.
5. Sync the BUILD node to the vault (markdown file).

Success gate (M3): 5 commits to any repo → 5 unique BUILD nodes
(or 1 BUILD node updated 5 times if commits share a repo — both are valid).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from synapse.graph.models import NodeType
from synapse.graph.operations import (
    create_edge,
    create_node,
    find_node_by_title,
    update_node,
)
from synapse.graph.vault_sync import write_node_file
from synapse.manifest import read_manifest, write_manifest


@dataclass
class GitIngestResult:
    """Result of processing a single git inbox item."""

    repo: str
    build_node_id: str
    edges_created: int = 0
    manifest_updated: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass
class GitIngestAgent:
    """Deterministic processor for `source=git` inbox items."""

    def process_item(self, frontmatter: dict[str, Any], body: str, capture_id: str) -> GitIngestResult:
        """Process one git capture and return a result.

        Args:
            frontmatter: Parsed YAML frontmatter from the inbox file.
            body:        Markdown body of the inbox item.
            capture_id:  Capture ID (used as source_id on new nodes).

        Returns:
            GitIngestResult describing what was created/updated.
        """
        repo = str(frontmatter.get("repo", "unknown-repo"))
        branch = str(frontmatter.get("branch", "main"))
        commit_hash = str(frontmatter.get("commit_hash", ""))
        repo_path_str: str | None = frontmatter.get("repo_path")

        # ── 1. Find or create BUILD node ──────────────────────────────────────
        try:
            build_node = self._find_or_create_build(
                repo=repo,
                branch=branch,
                commit_hash=commit_hash,
                body=body,
                capture_id=capture_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("git_ingest: failed to create/update BUILD for {r}", r=repo)
            return GitIngestResult(repo=repo, build_node_id="", error=str(exc))

        edges_created = 0
        manifest_updated = False

        # ── 2. Read manifest + wire CONCEPT edges ─────────────────────────────
        if repo_path_str:
            repo_path = Path(repo_path_str)
            try:
                edges_created, manifest_updated = self._apply_manifest(
                    repo_path=repo_path,
                    build_node_id=build_node.id,
                    commit_hash=commit_hash,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "git_ingest: manifest read/apply failed for {r}: {exc}",
                    r=repo,
                    exc=exc,
                )

        # ── 3. Vault sync ─────────────────────────────────────────────────────
        try:
            write_node_file(build_node)
        except Exception as exc:  # noqa: BLE001
            logger.warning("git_ingest: vault sync failed for {id}: {exc}", id=build_node.id, exc=exc)

        return GitIngestResult(
            repo=repo,
            build_node_id=build_node.id,
            edges_created=edges_created,
            manifest_updated=manifest_updated,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _find_or_create_build(
        self,
        *,
        repo: str,
        branch: str,
        commit_hash: str,
        body: str,
        capture_id: str,
    ):  # type: ignore[no-untyped-def]
        """Return a BUILD node for `repo`, creating one if absent."""
        existing = find_node_by_title(repo)
        short_hash = commit_hash[:7] if commit_hash else "unknown"

        if existing is not None and existing.type == NodeType.BUILD:
            # Append new commit info to the existing node.
            addition = f"\n---\n**Commit:** `{short_hash}` on `{branch}`\n\n{body}"
            node = update_node(
                existing.id,
                content_addition=addition,
                new_source_ids=[capture_id],
                new_tags=[branch],
            )
            logger.debug("git_ingest: updated BUILD {id} ({repo})", id=node.id, repo=repo)
            return node

        # First commit for this repo — create the BUILD node.
        content = f"**Latest:** `{short_hash}` on `{branch}`\n\n{body}"
        node = create_node(
            type=NodeType.BUILD,
            title=repo,
            content=content,
            source_ids=[capture_id],
            tags=[branch],
        )
        logger.debug("git_ingest: created BUILD {id} ({repo})", id=node.id, repo=repo)
        return node

    def _apply_manifest(
        self,
        *,
        repo_path: Path,
        build_node_id: str,
        commit_hash: str,
    ) -> tuple[int, bool]:
        """Read `synapse.json`, wire CONCEPT edges, update manifest.

        Returns:
            (edges_created, manifest_was_updated)
        """
        manifest = read_manifest(repo_path)
        if manifest is None:
            return 0, False

        edges_created = 0
        for concept_title in manifest.cs_concepts:
            concept = find_node_by_title(concept_title)
            if concept is None or concept.type != NodeType.CONCEPT:
                logger.info(
                    "git_ingest: concept {t!r} not found in graph; skipping edge",
                    t=concept_title,
                )
                continue
            try:
                create_edge(
                    source_node_id=concept.id,
                    target_node_id=build_node_id,
                    relation_type="applies_to",
                    weight=1.0,
                    created_by="git_ingest",
                    note=f"via synapse.json cs_concepts",
                )
                edges_created += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "git_ingest: edge {c} → {b} failed: {exc}",
                    c=concept_title,
                    b=build_node_id,
                    exc=exc,
                )

        # Write back updated commit hash + node id.
        manifest.last_commit = commit_hash
        manifest.synapse_node_id = build_node_id
        try:
            write_manifest(repo_path, manifest)
            return edges_created, True
        except OSError as exc:
            logger.warning("git_ingest: manifest write failed: {exc}", exc=exc)
            return edges_created, False


# Module-level singleton.
git_ingest_agent = GitIngestAgent()
