"""Vault sync tests — markdown file rendering + user-section preservation."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import yaml

from synapse.graph import operations
from synapse.graph.models import NodeType
from synapse.graph.vault_sync import (
    _USER_SECTION_MARKER,
    write_node_file,
)


@pytest.fixture(autouse=True)
def _stub_embeddings():  # type: ignore[no-untyped-def]
    with patch.object(operations, "_embed_and_upsert", lambda node: None):
        yield


def test_write_node_file_creates_markdown_with_frontmatter() -> None:
    node = operations.create_node(
        type="CONCEPT", title="Test Sync", content="some explanation"
    )
    path = write_node_file(node)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    body_after_fm = text.split("\n---\n", 1)[1]
    assert "# Test Sync" in body_after_fm
    assert "some explanation" in body_after_fm


def test_write_node_file_includes_user_section_marker() -> None:
    node = operations.create_node(type="CONCEPT", title="Marker Test", content="body")
    path = write_node_file(node)
    text = path.read_text(encoding="utf-8")
    assert _USER_SECTION_MARKER in text


def test_user_authored_content_preserved_on_resync() -> None:
    node = operations.create_node(
        type="CONCEPT", title="Preserve Test", content="original auto content"
    )
    path = write_node_file(node)

    # User edits the file: adds content below the marker.
    text = path.read_text(encoding="utf-8")
    text += "\n\nMy own notes here — must survive resync.\n"
    path.write_text(text, encoding="utf-8")

    # Now update the node and resync.
    operations.update_node(node.id, content_addition="new auto-generated paragraph")
    refreshed_node = operations.get_node(node.id)
    assert refreshed_node is not None
    write_node_file(refreshed_node)

    out = path.read_text(encoding="utf-8")
    assert "new auto-generated paragraph" in out
    assert "My own notes here" in out  # user content survived


def test_frontmatter_includes_retention_for_concepts() -> None:
    node = operations.create_node(type="CONCEPT", title="Retention FM", content="x")
    path = write_node_file(node)
    text = path.read_text(encoding="utf-8")
    fm_yaml = text.split("\n---\n", 1)[0][4:]
    fm = yaml.safe_load(fm_yaml)
    assert "retention" in fm
    assert "ease_factor" in fm["retention"]


def test_frontmatter_excludes_retention_for_non_concepts() -> None:
    node = operations.create_node(type="BUILD", title="Non Concept", content="b")
    path = write_node_file(node)
    text = path.read_text(encoding="utf-8")
    fm_yaml = text.split("\n---\n", 1)[0][4:]
    fm = yaml.safe_load(fm_yaml)
    assert "retention" not in fm
    assert fm["type"] == "BUILD"
