"""Clipboard polling daemon.

Polls the system clipboard every `CLIPBOARD_POLL_INTERVAL_SECONDS` seconds.
Captures text that:
    - is longer than `CLIPBOARD_MIN_LENGTH` characters,
    - differs from the most recent capture,
    - is not a hash-collision against the last `CLIPBOARD_DEDUP_WINDOW` captures,
    - does NOT match any of `CLIPBOARD_SKIP_PATTERNS` (credential-like content).

The daemon runs as a foreground async loop. It is launched headless on
Windows via `pythonw.exe` (no console) and auto-started via Task Scheduler;
the same loop runs as a systemd unit on Linux. See `synapse daemon install`.

PID + heartbeat live in `${VAULT}/.synapse/run/clipboard.pid` so external
supervisors can detect a hang.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import signal
import sys
import time
from collections import deque
from pathlib import Path

from loguru import logger

from synapse.capture.inbox import InboxWriteError, write_to_inbox
from synapse.config import (
    CLIPBOARD_DEDUP_WINDOW,
    CLIPBOARD_MIN_LENGTH,
    CLIPBOARD_POLL_INTERVAL_SECONDS,
    CLIPBOARD_SKIP_PATTERNS,
    get_settings,
)
from synapse.logging_setup import configure_logging

_SKIP_REGEXES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p) for p in CLIPBOARD_SKIP_PATTERNS
)

PID_FILENAME = "clipboard.pid"
HEARTBEAT_FILENAME = "clipboard.heartbeat"


def _hash(text: str) -> str:
    """Short stable hash for dedup."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _looks_sensitive(text: str) -> bool:
    """True if the clipboard content matches any credential-shaped pattern."""
    sample = text.strip()
    if not sample:
        return True
    # Run the credential regexes against the whole content AND a single-line
    # snapshot (some patterns are anchored).
    first_line = sample.splitlines()[0]
    return any(r.search(sample) or r.search(first_line) for r in _SKIP_REGEXES)


def _qualifies(text: str | None, recent: deque[str]) -> bool:
    """Apply all clipboard-capture qualification rules."""
    if text is None:
        return False
    stripped = text.strip()
    if len(stripped) < CLIPBOARD_MIN_LENGTH:
        return False
    if _looks_sensitive(stripped):
        return False
    if _hash(stripped) in recent:
        return False
    return True


def _read_clipboard() -> str | None:
    """Read clipboard contents; return None on any failure (clipboard libraries can
    raise on platforms with no display, locked sessions, etc.)."""
    try:
        import pyperclip  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("pyperclip not available — clipboard daemon disabled")
        return None
    try:
        return pyperclip.paste()
    except Exception as exc:  # noqa: BLE001
        logger.debug("clipboard read failed: {exc}", exc=exc)
        return None


def _write_pid_file(pid_dir: Path) -> Path:
    """Write our PID into `${pid_dir}/clipboard.pid` and return the path."""
    pid_dir.mkdir(parents=True, exist_ok=True)
    path = pid_dir / PID_FILENAME
    path.write_text(str(os.getpid()), encoding="utf-8")
    return path


def _heartbeat(pid_dir: Path) -> None:
    """Update the heartbeat timestamp file."""
    try:
        (pid_dir / HEARTBEAT_FILENAME).write_text(
            str(int(time.time())), encoding="utf-8"
        )
    except OSError:
        pass


async def _loop() -> None:
    """Main poll loop. Cancelled by SIGTERM / SIGINT."""
    settings = get_settings()
    pid_dir = settings.pid_dir
    pid_path = _write_pid_file(pid_dir)
    logger.info(
        "clipboard daemon started (pid {pid}, vault={vault})",
        pid=os.getpid(),
        vault=settings.synapse_vault_path,
    )

    recent: deque[str] = deque(maxlen=CLIPBOARD_DEDUP_WINDOW)
    last_seen: str | None = None

    try:
        while True:
            try:
                text = _read_clipboard()
                _heartbeat(pid_dir)

                if text is not None and text != last_seen:
                    last_seen = text
                    if _qualifies(text, recent):
                        try:
                            write_to_inbox(
                                source="clipboard",
                                content=text.strip(),
                            )
                            recent.append(_hash(text.strip()))
                        except InboxWriteError as exc:
                            logger.error("clipboard inbox write failed: {exc}", exc=exc)
                        except ValueError as exc:
                            logger.error("clipboard capture rejected: {exc}", exc=exc)
            except Exception as exc:  # noqa: BLE001
                logger.exception("clipboard poll iteration failed: {exc}", exc=exc)

            await asyncio.sleep(CLIPBOARD_POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        logger.info("clipboard daemon stopping (cancel)")
    finally:
        try:
            pid_path.unlink()
        except OSError:
            pass


def run() -> None:
    """Synchronous entry point. Used by `synapse daemon start` and Task Scheduler."""
    configure_logging(component="clipboard")

    if sys.platform != "win32":
        loop = asyncio.new_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: [t.cancel() for t in asyncio.all_tasks(loop)])
            except NotImplementedError:
                pass
        try:
            loop.run_until_complete(_loop())
        finally:
            loop.close()
    else:
        # Windows: SIGINT works via KeyboardInterrupt; no add_signal_handler.
        try:
            asyncio.run(_loop())
        except KeyboardInterrupt:
            logger.info("clipboard daemon stopping (KeyboardInterrupt)")


if __name__ == "__main__":
    run()
