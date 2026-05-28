"""`/ingest/*` — every capture endpoint funnels into `write_to_inbox()`.

Endpoints:
    POST /ingest/text     — universal text capture (used by manual CLI + dashboard quick-capture)
    POST /ingest/email    — Cloudflare Email Routing webhook (HMAC-verified)
    POST /ingest/browser  — browser extension capture (API key in header)
    POST /ingest/git      — git post-commit hook (M3 — stub in M0, accepts payload only)

Every endpoint is async, returns the capture id, and never blocks on slow I/O.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from synapse.capture.email_ingest import parse_email
from synapse.capture.inbox import InboxWriteError, write_to_inbox
from synapse.config import (
    BROWSER_API_KEY_HEADER,
    EMAIL_HMAC_HEADER,
    EMAIL_MAX_BODY_BYTES,
    get_settings,
)

router = APIRouter()


class IngestResponse(BaseModel):
    """Common response: capture id + relative inbox path."""

    capture_id: str
    inbox_path: str


# ── /ingest/text ─────────────────────────────────────────────────────────────


class TextIngestPayload(BaseModel):
    """Schema for `POST /ingest/text`."""

    content: str = Field(min_length=1, max_length=200_000)
    source: str = Field(default="manual")
    title: str | None = None
    tags: list[str] = Field(default_factory=list)


@router.post("/text", response_model=IngestResponse, status_code=status.HTTP_201_CREATED)
async def ingest_text(payload: TextIngestPayload) -> IngestResponse:
    """Capture an arbitrary text snippet."""
    extra: dict[str, Any] = {}
    if payload.title:
        extra["title"] = payload.title
    if payload.tags:
        extra["tags"] = payload.tags
    try:
        path = write_to_inbox(
            source=payload.source,
            content=payload.content,
            extra=extra,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InboxWriteError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return _response_from_path(path)


# ── /ingest/email ────────────────────────────────────────────────────────────


def _verify_hmac(body: bytes, header_value: str | None) -> None:
    """Constant-time HMAC verification against the configured secret."""
    secret = get_settings().synapse_email_webhook_secret
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="email webhook not configured (SYNAPSE_EMAIL_WEBHOOK_SECRET unset)",
        )
    if not header_value:
        raise HTTPException(status_code=401, detail="missing signature header")
    expected = hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, header_value.strip().lower()):
        raise HTTPException(status_code=401, detail="signature mismatch")


@router.post("/email", response_model=IngestResponse, status_code=status.HTTP_201_CREATED)
async def ingest_email(
    request: Request,
    signature: Annotated[str | None, Header(alias=EMAIL_HMAC_HEADER)] = None,
) -> IngestResponse:
    """Receive a raw RFC822 email (or JSON envelope) and write to inbox.

    Body may be:
        - `Content-Type: message/rfc822` raw email bytes
        - `Content-Type: application/json` with `{from, subject, body, ...}`
    """
    body = await request.body()
    if len(body) > EMAIL_MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="email body too large")
    _verify_hmac(body, signature)

    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        try:
            parsed = parse_email(json_payload=json.loads(body.decode("utf-8")))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"invalid JSON: {exc}") from exc
    else:
        parsed = parse_email(raw=body)

    extra = {
        "email_from": parsed.from_,
        "email_subject": parsed.subject,
        "email_message_id": parsed.message_id,
        "title": parsed.subject or "(no subject)",
    }
    if parsed.urls:
        extra["urls"] = parsed.urls

    try:
        path = write_to_inbox(source="email", content=parsed.clean_body, extra=extra)
    except InboxWriteError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return _response_from_path(path)


# ── /ingest/browser ──────────────────────────────────────────────────────────


class BrowserIngestPayload(BaseModel):
    """Schema for `POST /ingest/browser`.

    Accepts either the canonical field names (`selected_text`, `page_title`)
    OR the browser-extension shorthand (`content`, `title`) so the extension
    JS doesn't have to translate. Pydantic v2 AliasChoices resolves whichever
    is present; both can't be sent simultaneously — first match wins.
    """

    model_config = ConfigDict(populate_by_name=True)

    selected_text: str = Field(
        min_length=1,
        max_length=200_000,
        validation_alias=AliasChoices("selected_text", "content"),
    )
    page_title: str | None = Field(
        default=None,
        validation_alias=AliasChoices("page_title", "title"),
    )
    url: str | None = None


@router.post("/browser", response_model=IngestResponse, status_code=status.HTTP_201_CREATED)
async def ingest_browser(
    payload: BrowserIngestPayload,
    api_key: Annotated[str | None, Header(alias=BROWSER_API_KEY_HEADER)] = None,
) -> IngestResponse:
    """Capture a highlight from the browser extension."""
    expected = get_settings().synapse_browser_api_key
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="browser ingest not configured (SYNAPSE_BROWSER_API_KEY unset)",
        )
    if not api_key or not hmac.compare_digest(api_key, expected):
        raise HTTPException(status_code=401, detail="invalid API key")

    body_lines: list[str] = []
    if payload.url:
        body_lines.append(f"**Source:** [{payload.page_title or payload.url}]({payload.url})")
        body_lines.append("")
    body_lines.append(payload.selected_text)

    extra: dict[str, Any] = {}
    if payload.url:
        extra["source_url"] = payload.url
    if payload.page_title:
        extra["title"] = payload.page_title

    try:
        path = write_to_inbox(
            source="browser",
            content="\n".join(body_lines),
            extra=extra,
        )
    except InboxWriteError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return _response_from_path(path)


# ── /ingest/git (M3 wires this fully) ────────────────────────────────────────


class GitIngestPayload(BaseModel):
    """Schema for `POST /ingest/git` (git post-commit hook)."""

    repo: str
    branch: str
    commit_hash: str
    message: str
    files_changed: list[str] = Field(default_factory=list)
    lines_added: int = 0
    lines_removed: int = 0
    # Added in M3: the hook script passes its cwd so the Librarian can read
    # synapse.json from the repo root to wire BUILD↔CONCEPT edges.
    repo_path: str | None = None


@router.post("/git", response_model=IngestResponse, status_code=status.HTTP_201_CREATED)
async def ingest_git(payload: GitIngestPayload) -> IngestResponse:
    """Accept a git post-commit notification.

    In M0 this writes a placeholder inbox entry; the Librarian (M3) will
    promote it to a BUILD node and link to CONCEPT nodes.
    """
    body = (
        f"**Repo:** {payload.repo}\n"
        f"**Branch:** {payload.branch}\n"
        f"**Commit:** `{payload.commit_hash}`\n"
        f"**Files:** {len(payload.files_changed)} changed "
        f"(+{payload.lines_added} / -{payload.lines_removed})\n\n"
        f"{payload.message}\n"
    )
    extra: dict[str, Any] = {
        "repo": payload.repo,
        "branch": payload.branch,
        "commit_hash": payload.commit_hash,
        "files_changed": payload.files_changed,
        "lines_added": payload.lines_added,
        "lines_removed": payload.lines_removed,
        "title": f"{payload.repo}@{payload.commit_hash[:7]}",
    }
    if payload.repo_path is not None:
        extra["repo_path"] = payload.repo_path
    try:
        path = write_to_inbox(source="git", content=body, extra=extra)
    except InboxWriteError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _response_from_path(path)


# ── helpers ──────────────────────────────────────────────────────────────────


def _response_from_path(path) -> IngestResponse:  # type: ignore[no-untyped-def]
    """Build the standard ingest response from an inbox file path."""
    settings = get_settings()
    try:
        rel = path.relative_to(settings.synapse_vault_path)
        rel_str = rel.as_posix()
    except ValueError:
        rel_str = path.as_posix()
    # Capture id is the first frontmatter field, but the filename's short id
    # is sufficient for response correlation in M0.
    return IngestResponse(capture_id=path.stem, inbox_path=rel_str)
