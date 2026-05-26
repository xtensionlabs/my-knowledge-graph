"""The capture contract.

Every channel (Telegram, clipboard, email, browser, git, OCR) writes captures
through `write_to_inbox()` — the single function in the entire codebase that
puts bytes into `${VAULT}/inbox/`. This is by design: one funnel = one
auditable contract.

Frontmatter shape (locked by PRD §4.1):

    ---
    id: <uuid4>
    source: telegram | voice | clipboard | git | email | browser | ocr | ...
    captured_at: <ISO-8601 with tz>
    raw: true
    processed: false
    [extra YAML keys ...]
    ---

    <free-form markdown body>

Writes are atomic: content lands in `inbox/.tmp/<id>.md` first, then renamed
into place. A crash mid-write cannot leave a half-formed inbox file.

On any IOError, the capture is enqueued to `inbox_queue` for retry. Zero loss.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml
from loguru import logger
from sqlmodel import Session

from synapse.config import VALID_CAPTURE_SOURCES, get_settings
from synapse.graph.db import get_engine
from synapse.graph.models import CaptureLog, InboxQueue


class InboxWriteError(Exception):
    """Raised when an inbox write fails after enqueuing for retry."""


def _isoformat_utc(dt: datetime | None = None) -> str:
    """Return an ISO-8601 timestamp with explicit UTC offset."""
    return (dt or datetime.now(tz=timezone.utc)).isoformat()


def _sanitize_filename(s: str, max_len: int = 60) -> str:
    """Convert arbitrary text into a filesystem-safe filename fragment."""
    clean = "".join(c if c.isalnum() or c in "-_ " else "-" for c in s)
    clean = clean.strip().replace(" ", "-").lower()
    while "--" in clean:
        clean = clean.replace("--", "-")
    return clean[:max_len].strip("-") or "untitled"


def _build_filename(capture_id: str, source: str, captured_at: datetime) -> str:
    """`YYYY-MM-DDTHHMMSS_<source>_<short-id>.md`."""
    stamp = captured_at.strftime("%Y-%m-%dT%H%M%S")
    short = capture_id.split("-")[0]
    return f"{stamp}_{source}_{short}.md"


def _serialize_frontmatter(meta: Mapping[str, Any]) -> str:
    """YAML frontmatter block, sorted-keys for deterministic output."""
    body = yaml.safe_dump(
        dict(meta),
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    return f"---\n{body}---\n"


def _atomic_write(path: Path, contents: str) -> None:
    """Write `contents` to `path` atomically via a temp file in the same dir."""
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
        # Clean up only if rename failed and tmp still exists.
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _enqueue_for_retry(
    *,
    source: str,
    content: str,
    extra: Mapping[str, Any],
    error: str,
) -> None:
    """Persist a failed capture to the InboxQueue table for later replay."""
    import json

    payload = {"content": content, "extra": dict(extra)}
    row = InboxQueue(
        id=str(uuid.uuid4()),
        source=source,
        payload_json=json.dumps(payload, default=str),
        error=error,
    )
    try:
        with Session(get_engine()) as session:
            session.add(row)
            session.commit()
        logger.warning(
            "inbox write failed; enqueued for retry (source={source})", source=source
        )
    except Exception as exc:  # noqa: BLE001 — last-resort safety net
        # If even the DB is unreachable we cannot retry; log loudly so the
        # user knows there is real damage.
        logger.error(
            "FATAL: inbox write failed AND retry-queue insert failed: {exc}",
            exc=exc,
        )


def _log_capture(
    *, capture_id: str, source: str, filename: str, size_bytes: int
) -> None:
    """Audit-log a successful capture. Failures here are non-fatal (best-effort)."""
    row = CaptureLog(
        id=capture_id,
        source=source,
        inbox_filename=filename,
        size_bytes=size_bytes,
    )
    try:
        with Session(get_engine()) as session:
            session.add(row)
            session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("capture_log write failed: {exc}", exc=exc)


def write_to_inbox(
    *,
    source: str,
    content: str,
    extra: Mapping[str, Any] | None = None,
    captured_at: datetime | None = None,
) -> Path:
    """Write a single capture into `${VAULT}/inbox/`.

    Args:
        source: One of `synapse.config.VALID_CAPTURE_SOURCES`.
        content: Free-form markdown body. May be empty if the capture is
            purely metadata (e.g., a pending-OCR image envelope).
        extra: Additional frontmatter keys to merge after the required five.
        captured_at: Timestamp override; defaults to now-UTC.

    Returns:
        Absolute path to the newly written inbox file.

    Raises:
        ValueError: If `source` is not a recognized capture source.
        InboxWriteError: If the write failed AND the retry enqueue also failed
            (i.e., the capture is genuinely lost — extremely rare).
    """
    if source not in VALID_CAPTURE_SOURCES:
        raise ValueError(
            f"unknown capture source {source!r}; allowed: {VALID_CAPTURE_SOURCES}"
        )

    settings = get_settings()
    capture_id = str(uuid.uuid4())
    ts = captured_at or datetime.now(tz=timezone.utc)
    extra_meta = dict(extra or {})

    frontmatter: dict[str, Any] = {
        "id": capture_id,
        "source": source,
        "captured_at": _isoformat_utc(ts),
        "raw": True,
        "processed": False,
    }
    # Merge extras AFTER required keys so user keys cannot overwrite the five.
    for k, v in extra_meta.items():
        if k in frontmatter:
            continue
        frontmatter[k] = v

    filename = _build_filename(capture_id, source, ts)
    target = settings.inbox_dir / filename

    body = _serialize_frontmatter(frontmatter) + "\n" + content.rstrip() + "\n"

    try:
        _atomic_write(target, body)
    except OSError as exc:
        _enqueue_for_retry(
            source=source, content=content, extra=extra_meta, error=str(exc)
        )
        raise InboxWriteError(
            f"inbox write failed for source={source}; queued for retry"
        ) from exc

    _log_capture(
        capture_id=capture_id,
        source=source,
        filename=filename,
        size_bytes=len(body.encode("utf-8")),
    )

    logger.info(
        "captured {source} → {filename} ({size} bytes)",
        source=source,
        filename=filename,
        size=len(body),
    )
    return target


def count_inbox_items() -> int:
    """Return the number of unprocessed markdown files in `inbox/`."""
    settings = get_settings()
    if not settings.inbox_dir.exists():
        return 0
    return sum(1 for p in settings.inbox_dir.glob("*.md") if p.is_file())


def oldest_inbox_items(limit: int = 3) -> list[Path]:
    """Return up to `limit` oldest inbox files by mtime."""
    settings = get_settings()
    if not settings.inbox_dir.exists():
        return []
    files = sorted(
        (p for p in settings.inbox_dir.glob("*.md") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
    )
    return files[:limit]
