"""Tests for the inbox writer — the single capture contract."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from synapse.capture.inbox import (
    InboxWriteError,
    count_inbox_items,
    oldest_inbox_items,
    write_to_inbox,
)
from synapse.config import INBOX_FRONTMATTER_KEYS, get_settings


def _read_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"missing frontmatter delimiter in {path}"
    end = text.index("\n---\n", 4)
    return yaml.safe_load(text[4:end])


def test_write_to_inbox_creates_file_with_required_frontmatter() -> None:
    path = write_to_inbox(source="telegram", content="hello world")
    assert path.exists()
    fm = _read_frontmatter(path)
    for key in INBOX_FRONTMATTER_KEYS:
        assert key in fm, f"missing required key {key}"
    assert fm["source"] == "telegram"
    assert fm["raw"] is True
    assert fm["processed"] is False
    # captured_at must be ISO-8601 with timezone info
    assert "+" in fm["captured_at"] or fm["captured_at"].endswith("Z")


def test_write_to_inbox_rejects_unknown_source() -> None:
    with pytest.raises(ValueError, match="unknown capture source"):
        write_to_inbox(source="invented-source", content="x")


def test_write_to_inbox_preserves_extra_metadata() -> None:
    path = write_to_inbox(
        source="email",
        content="body",
        extra={"email_from": "prof@uni.edu", "title": "exam reminder"},
    )
    fm = _read_frontmatter(path)
    assert fm["email_from"] == "prof@uni.edu"
    assert fm["title"] == "exam reminder"


def test_extras_cannot_overwrite_required_keys() -> None:
    path = write_to_inbox(
        source="manual",
        content="x",
        extra={"id": "ATTACKER", "processed": True, "source": "INJECTED"},
    )
    fm = _read_frontmatter(path)
    assert fm["id"] != "ATTACKER"
    assert fm["processed"] is False
    assert fm["source"] == "manual"


def test_count_and_oldest_inbox_items() -> None:
    assert count_inbox_items() == 0
    for i in range(3):
        write_to_inbox(source="manual", content=f"item {i}")
    assert count_inbox_items() == 3
    oldest = oldest_inbox_items(limit=2)
    assert len(oldest) == 2


def test_atomic_write_no_partial_files() -> None:
    """A successful write must leave no .partial files behind."""
    write_to_inbox(source="manual", content="x")
    inbox_dir = get_settings().inbox_dir
    tmp_dir = inbox_dir / ".tmp"
    leftovers = list(tmp_dir.glob("*.partial")) if tmp_dir.exists() else []
    assert leftovers == []


def test_inbox_write_failure_enqueues_to_retry_queue(monkeypatch) -> None:
    """When os.replace fails, the capture must land in InboxQueue, not vanish."""
    import os
    from sqlmodel import Session, select
    from synapse.capture import inbox as inbox_mod
    from synapse.graph.db import get_engine
    from synapse.graph.models import InboxQueue

    def boom(_src, _dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(InboxWriteError):
        write_to_inbox(source="telegram", content="will fail")

    with Session(get_engine()) as session:
        rows = session.exec(select(InboxQueue)).all()
    assert len(rows) == 1
    assert rows[0].source == "telegram"
    assert "will fail" in rows[0].payload_json
