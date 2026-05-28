"""Tests for `/ingest/text`, `/ingest/email`, `/ingest/browser`, `/ingest/git`."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from httpx import ASGITransport, AsyncClient

from synapse.capture.inbox import count_inbox_items
from synapse.config import (
    BROWSER_API_KEY_HEADER,
    EMAIL_HMAC_HEADER,
    get_settings,
)


@pytest.mark.asyncio
async def test_ingest_text_creates_inbox_file(fastapi_app) -> None:  # type: ignore[no-untyped-def]
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://t") as c:
        resp = await c.post(
            "/ingest/text",
            json={"content": "graph theory is great", "source": "manual"},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["capture_id"]
    assert body["inbox_path"].startswith("inbox/")
    assert count_inbox_items() == 1


@pytest.mark.asyncio
async def test_ingest_text_rejects_unknown_source(fastapi_app) -> None:  # type: ignore[no-untyped-def]
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://t") as c:
        resp = await c.post(
            "/ingest/text",
            json={"content": "x", "source": "not-a-real-source"},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_ingest_email_rejects_missing_signature(fastapi_app) -> None:  # type: ignore[no-untyped-def]
    body = json.dumps({"from": "p@x", "subject": "s", "body": "hi"}).encode()
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://t") as c:
        resp = await c.post(
            "/ingest/email",
            content=body,
            headers={"content-type": "application/json"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_ingest_email_rejects_bad_signature(fastapi_app) -> None:  # type: ignore[no-untyped-def]
    body = json.dumps({"from": "p@x", "subject": "s", "body": "hi"}).encode()
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://t") as c:
        resp = await c.post(
            "/ingest/email",
            content=body,
            headers={"content-type": "application/json", EMAIL_HMAC_HEADER: "deadbeef"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_ingest_email_accepts_valid_signature(fastapi_app) -> None:  # type: ignore[no-untyped-def]
    secret = get_settings().synapse_email_webhook_secret
    payload = {
        "from": "professor@strathmore.edu",
        "subject": "ICS1104 — week 5",
        "body": "Reminder: CAT on Friday\n\nOn Mon, Bob wrote:\n> ignore me",
        "message_id": "<m1@x>",
    }
    body = json.dumps(payload).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://t") as c:
        resp = await c.post(
            "/ingest/email",
            content=body,
            headers={"content-type": "application/json", EMAIL_HMAC_HEADER: sig},
        )
    assert resp.status_code == 201, resp.text
    assert count_inbox_items() == 1


@pytest.mark.asyncio
async def test_ingest_browser_requires_api_key(fastapi_app) -> None:  # type: ignore[no-untyped-def]
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://t") as c:
        resp = await c.post(
            "/ingest/browser",
            json={"selected_text": "an interesting paragraph from the page"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_ingest_browser_accepts_valid_key(fastapi_app) -> None:  # type: ignore[no-untyped-def]
    key = get_settings().synapse_browser_api_key
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://t") as c:
        resp = await c.post(
            "/ingest/browser",
            json={
                "selected_text": "an interesting paragraph",
                "page_title": "Some Page",
                "url": "https://example.com/x",
            },
            headers={BROWSER_API_KEY_HEADER: key},
        )
    assert resp.status_code == 201, resp.text
    assert count_inbox_items() == 1


@pytest.mark.asyncio
async def test_ingest_browser_accepts_extension_field_names(fastapi_app) -> None:  # type: ignore[no-untyped-def]
    """The browser extension sends `content` + `title` (its own naming).
    The endpoint must accept these as aliases for `selected_text` + `page_title`.
    """
    key = get_settings().synapse_browser_api_key
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://t") as c:
        resp = await c.post(
            "/ingest/browser",
            json={
                "content": "a paragraph the extension scraped",
                "title": "Page from the extension",
                "url": "https://example.com/y",
            },
            headers={BROWSER_API_KEY_HEADER: key},
        )
    assert resp.status_code == 201, resp.text
    assert count_inbox_items() == 1


@pytest.mark.asyncio
async def test_ingest_git_creates_capture(fastapi_app) -> None:  # type: ignore[no-untyped-def]
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://t") as c:
        resp = await c.post(
            "/ingest/git",
            json={
                "repo": "xtension-signal",
                "branch": "main",
                "commit_hash": "abc1234def567890",
                "message": "feat: add capture envelope",
                "files_changed": ["synapse/capture/inbox.py"],
                "lines_added": 42,
                "lines_removed": 3,
            },
        )
    assert resp.status_code == 201, resp.text
    assert count_inbox_items() == 1
