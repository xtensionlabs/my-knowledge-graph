"""Tests for synapse/manifest.py — SynapseManifest schema + read/write."""

from __future__ import annotations

from pathlib import Path

import pytest

from synapse.manifest import SynapseManifest, read_manifest, write_manifest


# ── Schema tests ──────────────────────────────────────────────────────────────


def test_manifest_defaults() -> None:
    m = SynapseManifest()
    assert m.module == ""
    assert m.description == ""
    assert m.cs_concepts == []
    assert m.open_questions == []
    assert m.last_commit == ""
    assert m.synapse_node_id == ""


def test_manifest_full_fields() -> None:
    m = SynapseManifest(
        module="xtension-signal",
        description="Real-time analytics",
        cs_concepts=["BFS", "event-driven architecture"],
        open_questions=["How to handle backpressure?"],
        last_commit="abc1234",
        synapse_node_id="uuid-of-node",
    )
    assert m.module == "xtension-signal"
    assert "BFS" in m.cs_concepts
    assert len(m.open_questions) == 1


# ── I/O tests ─────────────────────────────────────────────────────────────────


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    m = SynapseManifest(
        module="my-repo",
        description="Test repo",
        cs_concepts=["BFS", "DFS"],
        open_questions=["Why is this O(n)?"],
        last_commit="deadbeef",
        synapse_node_id="node-uuid-123",
    )
    write_manifest(tmp_path, m)
    loaded = read_manifest(tmp_path)
    assert loaded is not None
    assert loaded.module == "my-repo"
    assert loaded.cs_concepts == ["BFS", "DFS"]
    assert loaded.last_commit == "deadbeef"
    assert loaded.synapse_node_id == "node-uuid-123"


def test_read_manifest_missing_returns_none(tmp_path: Path) -> None:
    assert read_manifest(tmp_path) is None


def test_read_manifest_malformed_json_returns_none(tmp_path: Path) -> None:
    (tmp_path / "synapse.json").write_text("{ not: valid json }", encoding="utf-8")
    assert read_manifest(tmp_path) is None


def test_write_manifest_is_atomic(tmp_path: Path) -> None:
    """write_manifest must not leave a .tmp file behind."""
    m = SynapseManifest(module="atomic-test")
    write_manifest(tmp_path, m)
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == [], f"leftover temp files: {tmp_files}"
    manifest_file = tmp_path / "synapse.json"
    assert manifest_file.is_file()


def test_write_manifest_overwrites(tmp_path: Path) -> None:
    m1 = SynapseManifest(module="v1")
    m2 = SynapseManifest(module="v2", last_commit="new-sha")
    write_manifest(tmp_path, m1)
    write_manifest(tmp_path, m2)
    loaded = read_manifest(tmp_path)
    assert loaded is not None
    assert loaded.module == "v2"
    assert loaded.last_commit == "new-sha"


def test_manifest_file_is_valid_json(tmp_path: Path) -> None:
    import json

    m = SynapseManifest(module="json-check", cs_concepts=["BFS"])
    write_manifest(tmp_path, m)
    raw = (tmp_path / "synapse.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data["module"] == "json-check"
    assert "BFS" in data["cs_concepts"]
