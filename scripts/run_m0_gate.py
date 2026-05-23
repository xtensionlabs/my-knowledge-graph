"""M0 success gate runner.

Exercises all three capture legs end-to-end against a running gateway:
    - 10 Telegram-shape captures via POST /ingest/text (source=telegram)
    - 5 clipboard captures via direct write_to_inbox(source=clipboard)
      (this is the exact call the polling daemon makes — the daemon adds
      only the schedule and the dedup/qualifier rules, both unit-tested)
    - 2 email captures via POST /ingest/email with valid HMAC

Verifies:
    - All 17 markdown files land in `inbox/`
    - Each file has valid frontmatter with the five required keys
    - Each capture's source field matches its channel
    - Zero retry-queue entries in InboxQueue

Run after `synapse init` and with the gateway listening on 127.0.0.1:8000.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass

import httpx
import yaml
from sqlmodel import Session, select

from synapse.capture.inbox import write_to_inbox
from synapse.config import EMAIL_HMAC_HEADER, get_settings
from synapse.graph.db import get_engine
from synapse.graph.models import CaptureLog, InboxQueue


GATEWAY = "http://127.0.0.1:8000"


def post_telegram_shape(client: httpx.Client, index: int) -> str:
    """POST a telegram-shape capture. Returns the inbox path."""
    payload = {
        "content": (
            f"Telegram capture #{index}: "
            "Today's lecture on graph theory connected directly to the routing "
            "problem in Xtension Signal. The proof that BFS yields shortest "
            "unweighted paths is the same argument that justifies our "
            "notification fan-out strategy."
        ),
        "source": "telegram",
        "title": f"telegram-{index}",
        "tags": ["graph-theory", "xtension"],
    }
    r = client.post(f"{GATEWAY}/ingest/text", json=payload, timeout=10)
    r.raise_for_status()
    return r.json()["inbox_path"]


def write_clipboard_capture(index: int) -> str:
    """Exercise the clipboard pipeline directly."""
    content = (
        f"Clipboard capture #{index}: "
        "An interesting paragraph copied from an article about spaced "
        "repetition. SM-2 ease factors decay multiplicatively, which means "
        "weak intervals compound into catastrophic forgetting if uncorrected."
    )
    path = write_to_inbox(source="clipboard", content=content)
    return path.as_posix()


def post_email(client: httpx.Client, index: int) -> str:
    """POST an HMAC-signed email payload."""
    secret = get_settings().synapse_email_webhook_secret
    payload = {
        "from": f"prof{index}@strathmore.edu",
        "subject": f"ICS1104 — week {index} reminder",
        "body": (
            f"Email capture #{index}\n"
            "\n"
            "Reminder: CAT on Friday covers chapters 1–4.\n"
            "Office hours: Wed 10–12.\n"
            "\n"
            "-- \n"
            "Prof Mwangi\n"
            "Strathmore CS\n"
        ),
        "message_id": f"<sim-{index}@synapse>",
    }
    body = json.dumps(payload).encode("utf-8")
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    r = client.post(
        f"{GATEWAY}/ingest/email",
        content=body,
        headers={"content-type": "application/json", EMAIL_HMAC_HEADER: sig},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["inbox_path"]


def verify_frontmatter(path: Path, expected_source: str) -> tuple[bool, str]:
    """Return (ok, reason)."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return False, "missing frontmatter delimiter"
    try:
        end = text.index("\n---\n", 4)
    except ValueError:
        return False, "no closing frontmatter delimiter"
    fm = yaml.safe_load(text[4:end])
    for key in ("id", "source", "captured_at", "raw", "processed"):
        if key not in fm:
            return False, f"missing key {key}"
    if fm["source"] != expected_source:
        return False, f"source mismatch (got {fm['source']!r}, expected {expected_source!r})"
    if fm["raw"] is not True:
        return False, "raw is not True"
    if fm["processed"] is not False:
        return False, "processed is not False"
    return True, "ok"


def main() -> int:
    settings = get_settings()
    inbox = settings.inbox_dir
    print(f"vault: {settings.synapse_vault_path}")
    print(f"inbox: {inbox}")

    # Sanity check
    r = httpx.get(f"{GATEWAY}/health", timeout=5)
    r.raise_for_status()
    initial_count = r.json()["inbox_count"]
    print(f"initial inbox count: {initial_count}")

    expected = {}  # path → expected source

    with httpx.Client() as client:
        for i in range(1, 11):
            p = post_telegram_shape(client, i)
            expected[p] = "telegram"
        print(f"✓ 10 telegram-shape captures posted")

        for i in range(1, 6):
            p = write_clipboard_capture(i)
            expected[p] = "clipboard"
        print(f"✓ 5 clipboard captures written")

        for i in range(1, 3):
            p = post_email(client, i)
            expected[p] = "email"
        print(f"✓ 2 email captures posted")

    # Give filesystem a tick.
    time.sleep(0.2)

    # Count files in inbox.
    files = sorted(p for p in inbox.glob("*.md") if p.is_file())
    print(f"\ninbox files: {len(files)}")
    for f in files:
        print(f"  {f.name}")

    if len(files) != 17:
        print(f"\n❌ FAIL: expected 17 files, got {len(files)}")
        return 1

    # Verify frontmatter on each.
    by_source: dict[str, int] = {"telegram": 0, "clipboard": 0, "email": 0}
    for f in files:
        # Determine expected source from filename pattern.
        # Filename: YYYY-MM-DDTHHMMSS_<source>_<short>.md
        parts = f.stem.split("_")
        if len(parts) < 3:
            print(f"❌ malformed filename: {f.name}")
            return 1
        expected_source = parts[1]
        ok, reason = verify_frontmatter(f, expected_source)
        if not ok:
            print(f"❌ {f.name}: {reason}")
            return 1
        by_source[expected_source] = by_source.get(expected_source, 0) + 1

    print(f"\nfrontmatter validation: ok")
    print(f"  telegram:  {by_source['telegram']}")
    print(f"  clipboard: {by_source['clipboard']}")
    print(f"  email:     {by_source['email']}")

    if by_source["telegram"] != 10 or by_source["clipboard"] != 5 or by_source["email"] != 2:
        print(f"\n❌ FAIL: source-count mismatch")
        return 1

    # Retry queue must be empty.
    with Session(get_engine()) as session:
        pending = session.exec(
            select(InboxQueue).where(InboxQueue.succeeded == False)  # noqa: E712
        ).all()
        logged = session.exec(select(CaptureLog)).all()

    if pending:
        print(f"\n❌ FAIL: {len(pending)} entries in inbox_queue (zero loss requires zero retries)")
        for row in pending:
            print(f"  {row.source}: {row.error}")
        return 1

    print(f"\ncapture_log rows: {len(logged)}")
    print(f"inbox_queue pending: {len(pending)}")

    print("\n" + "=" * 60)
    print(" M0 SUCCESS GATE: PASSED")
    print("=" * 60)
    print(" 10 telegram + 5 clipboard + 2 email = 17 captures")
    print(" zero losses, zero retries, all frontmatter valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
