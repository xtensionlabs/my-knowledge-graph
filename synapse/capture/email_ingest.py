"""Email parsing for `POST /ingest/email`.

Accepts either raw RFC822 bytes (the Cloudflare Email Routing default) or a
pre-parsed JSON envelope (used by the `synapse simulate email` CLI for local
M0 validation without DNS). Returns a `ParsedEmail` with quoted-reply and
signature blocks stripped, suitable for the inbox body.

`mail-parser` is the locked dependency for RFC822 parsing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

# URL extraction
_URL_RE = re.compile(r"https?://[^\s<>\"']+")

# Quoted-reply detection — first line that matches any of these triggers truncation.
_QUOTE_PATTERNS = (
    re.compile(r"^On .+ wrote:\s*$", re.IGNORECASE),
    re.compile(r"^-----Original Message-----\s*$", re.IGNORECASE),
    re.compile(r"^From: .+$", re.IGNORECASE),
    re.compile(r"^Sent from my .+$", re.IGNORECASE),
)

# Signature detection — content from the first `-- ` marker line onward is dropped.
_SIG_DELIMITER = re.compile(r"^--\s*$")


@dataclass
class ParsedEmail:
    """The minimum we need from an inbound email."""

    from_: str = ""
    subject: str = ""
    message_id: str = ""
    raw_body: str = ""
    clean_body: str = ""
    urls: list[str] = field(default_factory=list)
    attachments: list[dict[str, Any]] = field(default_factory=list)


def _strip_quotes_and_sig(body: str) -> str:
    """Drop quoted replies and trailing signatures."""
    lines = body.splitlines()
    kept: list[str] = []
    for line in lines:
        if _SIG_DELIMITER.match(line):
            break
        if any(pat.match(line) for pat in _QUOTE_PATTERNS):
            break
        # Drop quoted prefix lines (`>` quoting).
        if line.lstrip().startswith(">"):
            continue
        kept.append(line)
    # Collapse runs of blank lines.
    cleaned: list[str] = []
    blank = False
    for line in kept:
        if line.strip() == "":
            if blank:
                continue
            blank = True
        else:
            blank = False
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def _extract_urls(text: str) -> list[str]:
    """Return de-duplicated URLs in order of first occurrence."""
    seen: dict[str, None] = {}
    for m in _URL_RE.finditer(text):
        seen.setdefault(m.group(0).rstrip(".,);]"), None)
    return list(seen.keys())


def _parse_rfc822(raw: bytes) -> ParsedEmail:
    """Use mail-parser for full RFC822 decode; fall back to stdlib `email`."""
    try:
        import mailparser  # type: ignore[import-untyped]

        m = mailparser.parse_from_bytes(raw)
        from_ = ""
        if m.from_:
            try:
                first = m.from_[0]
                from_ = first[1] if isinstance(first, (tuple, list)) and len(first) >= 2 else str(first)
            except (IndexError, TypeError):
                from_ = str(m.from_)
        body_text = m.body or m.text_plain[0] if m.text_plain else (m.body or "")
        return ParsedEmail(
            from_=from_,
            subject=(m.subject or "").strip(),
            message_id=(m.message_id or "").strip(),
            raw_body=body_text or "",
            clean_body=_strip_quotes_and_sig(body_text or ""),
            urls=_extract_urls(body_text or ""),
            attachments=[
                {"filename": a.get("filename", ""), "mail_content_type": a.get("mail_content_type", "")}
                for a in (m.attachments or [])
            ],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("mail-parser failed ({exc}); falling back to stdlib email", exc=exc)
        return _parse_with_stdlib(raw)


def _parse_with_stdlib(raw: bytes) -> ParsedEmail:
    """Minimal stdlib fallback if mail-parser is unavailable or errors."""
    import email
    from email import policy

    msg = email.message_from_bytes(raw, policy=policy.default)
    body_text = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                body_text = part.get_content()
                break
    else:
        body_text = msg.get_content() if msg.get_content_type() == "text/plain" else ""

    return ParsedEmail(
        from_=str(msg.get("From", "")),
        subject=str(msg.get("Subject", "")),
        message_id=str(msg.get("Message-ID", "")),
        raw_body=body_text or "",
        clean_body=_strip_quotes_and_sig(body_text or ""),
        urls=_extract_urls(body_text or ""),
    )


def _parse_json(payload: dict[str, Any]) -> ParsedEmail:
    """Build a ParsedEmail from a pre-parsed JSON envelope.

    Expected shape (loose — accepts variants used by Cloudflare workers and the
    `synapse simulate email` CLI):

        {
          "from": "...",
          "subject": "...",
          "body": "...",
          "message_id": "...",
          "attachments": [...]
        }
    """
    body = str(payload.get("body") or payload.get("text") or "")
    return ParsedEmail(
        from_=str(payload.get("from", "")),
        subject=str(payload.get("subject", "")),
        message_id=str(payload.get("message_id", "") or payload.get("id", "")),
        raw_body=body,
        clean_body=_strip_quotes_and_sig(body),
        urls=_extract_urls(body),
        attachments=list(payload.get("attachments", []) or []),
    )


def parse_email(
    *, raw: bytes | None = None, json_payload: dict[str, Any] | None = None
) -> ParsedEmail:
    """Parse an inbound email into a `ParsedEmail`.

    Args:
        raw: Raw RFC822 bytes (mutually exclusive with `json_payload`).
        json_payload: Pre-parsed JSON envelope (used by the simulate CLI).

    Returns:
        A populated `ParsedEmail`. Always returns — never raises on parsing
        edge cases; falls back to empty fields with warnings logged.
    """
    if (raw is None) == (json_payload is None):
        raise ValueError("parse_email requires exactly one of raw= or json_payload=")
    if json_payload is not None:
        return _parse_json(json_payload)
    assert raw is not None  # for type-checker
    return _parse_rfc822(raw)
