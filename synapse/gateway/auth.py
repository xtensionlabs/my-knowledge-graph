"""Gateway credential store — Fernet-encrypted OAuth tokens.

This is the ONLY module in Synapse that touches raw OAuth tokens (CLAUDE.md
non-negotiable rule 5). Other modules call the integration proxies (e.g.,
`synapse.integrations.google_calendar.list_upcoming_events()`) which in turn
call `with_token(...)` here to obtain a short-lived bearer string.

Token storage uses `cryptography.fernet` with the master key from
`SYNAPSE_SECRET_KEY` (32 url-safe base64 bytes, generated once at vault init
and never logged). The encrypted ciphertext lives in the `credentials` table.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken
from loguru import logger
from sqlmodel import Session, select

from synapse.config import (
    ANTHROPIC_CONNECT_TIMEOUT_SECONDS,
    GOOGLE_OAUTH_AUTHORIZE_URL,
    GOOGLE_OAUTH_REDIRECT_PATH,
    GOOGLE_OAUTH_SCOPES,
    GOOGLE_OAUTH_TOKEN_URL,
    OAUTH_TOKEN_REFRESH_LEEWAY_SECONDS,
    get_settings,
)
from synapse.graph.db import get_engine
from synapse.graph.models import Credential


class AuthError(Exception):
    """Raised for auth-layer failures (missing creds, invalid token, etc)."""


# ── Fernet key derivation ────────────────────────────────────────────────────


def _fernet() -> Fernet:
    """Return a Fernet instance derived from SYNAPSE_SECRET_KEY.

    The secret key in `.env` need not be a valid Fernet key directly; we
    SHA-256 it and url-safe-base64-encode, so any non-empty string yields a
    deterministic key. (This mirrors the same hardening used in M0.)
    """
    settings = get_settings()
    raw = settings.synapse_secret_key
    if not raw:
        raise AuthError(
            "SYNAPSE_SECRET_KEY is unset; cannot encrypt/decrypt OAuth tokens"
        )
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def _decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise AuthError("token decryption failed — wrong SYNAPSE_SECRET_KEY?") from exc


# ── OAuth flow primitives ────────────────────────────────────────────────────


@dataclass
class AuthorizationStart:
    """Returned by `start_authorization()` — the URL the user visits + state."""

    authorize_url: str
    state: str


def _redirect_uri() -> str:
    """Construct the gateway-side redirect URI for OAuth callbacks."""
    settings = get_settings()
    return (
        f"http://{settings.synapse_gateway_host}:{settings.synapse_gateway_port}"
        f"{GOOGLE_OAUTH_REDIRECT_PATH}"
    )


def start_authorization(service: str = "google_calendar") -> AuthorizationStart:
    """Build the OAuth authorize URL the user must visit in a browser.

    Args:
        service: Logical service name; for now only `google_calendar`.

    Returns:
        AuthorizationStart with URL and a CSRF-protection `state` token.

    Raises:
        AuthError: If Google client ID is not configured.
    """
    settings = get_settings()
    if not settings.google_client_id:
        raise AuthError(
            "GOOGLE_CLIENT_ID is unset; configure it in .env before starting OAuth"
        )

    state = secrets.token_urlsafe(24)
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": " ".join(GOOGLE_OAUTH_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    url = f"{GOOGLE_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"

    # Persist `state` in a separate `Credential` row of service=`oauth_state:{service}`
    # so the callback can verify it without an extra table.
    with Session(get_engine()) as session:
        row = Credential(
            id=str(uuid.uuid4()),
            service=f"oauth_state:{service}",
            access_token=_encrypt(state),
            refresh_token="",
            expires_at=datetime.now(tz=timezone.utc) + timedelta(minutes=10),
            scopes=json.dumps(list(GOOGLE_OAUTH_SCOPES)),
        )
        session.add(row)
        session.commit()

    return AuthorizationStart(authorize_url=url, state=state)


def _verify_state(service: str, state: str) -> None:
    """Pop the matching state row (single use). Raises AuthError if not found."""
    with Session(get_engine()) as session:
        rows = list(
            session.exec(
                select(Credential).where(
                    Credential.service == f"oauth_state:{service}"
                )
            ).all()
        )
        for row in rows:
            try:
                stored = _decrypt(row.access_token)
            except AuthError:
                continue
            if secrets.compare_digest(stored, state):
                session.delete(row)
                session.commit()
                return
    raise AuthError("invalid or expired OAuth state")


def _exchange_code_for_tokens(
    code: str,
    *,
    token_url: str = GOOGLE_OAUTH_TOKEN_URL,
    http_client: httpx.Client | None = None,
) -> dict[str, Any]:
    """POST to Google's token endpoint to exchange code → access+refresh tokens."""
    settings = get_settings()
    payload = {
        "code": code,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "redirect_uri": _redirect_uri(),
        "grant_type": "authorization_code",
    }
    client = http_client or httpx.Client(timeout=ANTHROPIC_CONNECT_TIMEOUT_SECONDS)
    try:
        resp = client.post(token_url, data=payload)
        if resp.status_code >= 400:
            raise AuthError(f"token exchange failed: {resp.status_code} {resp.text[:200]}")
        return resp.json()
    finally:
        if http_client is None:
            client.close()


def complete_authorization(
    *,
    service: str,
    code: str,
    state: str,
    http_client: httpx.Client | None = None,
) -> Credential:
    """Finalise an OAuth flow: verify state, exchange code, store encrypted tokens.

    Args:
        service: Logical service name (e.g., "google_calendar").
        code:    Authorization code from the OAuth callback.
        state:   CSRF state echoed by the provider.
        http_client: Optional httpx client (tests inject a mock).

    Returns:
        The persisted Credential row.

    Raises:
        AuthError: On state mismatch or token-exchange failure.
    """
    _verify_state(service, state)
    token_payload = _exchange_code_for_tokens(code, http_client=http_client)

    access_token = token_payload.get("access_token")
    refresh_token = token_payload.get("refresh_token", "")
    expires_in = int(token_payload.get("expires_in", 3600))
    scopes = token_payload.get("scope", " ".join(GOOGLE_OAUTH_SCOPES)).split()
    if not access_token:
        raise AuthError("token exchange returned no access_token")

    expires_at = datetime.now(tz=timezone.utc) + timedelta(seconds=expires_in)

    with Session(get_engine()) as session:
        # Replace any existing row for this service (one credential per service).
        existing = session.exec(
            select(Credential).where(Credential.service == service)
        ).first()
        if existing is not None:
            existing.access_token = _encrypt(access_token)
            if refresh_token:
                existing.refresh_token = _encrypt(refresh_token)
            existing.expires_at = expires_at
            existing.scopes = json.dumps(list(scopes))
            existing.updated_at = datetime.now(tz=timezone.utc)
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing

        row = Credential(
            id=str(uuid.uuid4()),
            service=service,
            access_token=_encrypt(access_token),
            refresh_token=_encrypt(refresh_token) if refresh_token else "",
            expires_at=expires_at,
            scopes=json.dumps(list(scopes)),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row


def _refresh_access_token(
    cred: Credential, *, http_client: httpx.Client | None = None
) -> Credential:
    """Use the stored refresh_token to mint a new access_token. Mutates cred."""
    if not cred.refresh_token:
        raise AuthError(f"{cred.service}: no refresh_token stored; user must reauthorize")
    settings = get_settings()
    payload = {
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "refresh_token": _decrypt(cred.refresh_token),
        "grant_type": "refresh_token",
    }
    client = http_client or httpx.Client(timeout=ANTHROPIC_CONNECT_TIMEOUT_SECONDS)
    try:
        resp = client.post(GOOGLE_OAUTH_TOKEN_URL, data=payload)
        if resp.status_code >= 400:
            raise AuthError(
                f"refresh failed for {cred.service}: {resp.status_code} {resp.text[:200]}"
            )
        data = resp.json()
    finally:
        if http_client is None:
            client.close()

    new_access = data.get("access_token")
    expires_in = int(data.get("expires_in", 3600))
    if not new_access:
        raise AuthError("refresh response missing access_token")

    with Session(get_engine()) as session:
        row = session.get(Credential, cred.id)
        if row is None:
            raise AuthError(f"credential {cred.id!r} disappeared mid-refresh")
        row.access_token = _encrypt(new_access)
        row.expires_at = datetime.now(tz=timezone.utc) + timedelta(seconds=expires_in)
        row.updated_at = datetime.now(tz=timezone.utc)
        session.add(row)
        session.commit()
        session.refresh(row)
        return row


def get_access_token(service: str, *, http_client: httpx.Client | None = None) -> str:
    """Return a fresh, decrypted access_token for `service`.

    Automatically refreshes if the stored token is within
    `OAUTH_TOKEN_REFRESH_LEEWAY_SECONDS` of expiry.

    Args:
        service: The service name (e.g., "google_calendar").
        http_client: Optional injected client (for tests).

    Returns:
        The plaintext access token. Caller must not log or persist it.

    Raises:
        AuthError: If no credential is stored or refresh fails.
    """
    with Session(get_engine()) as session:
        cred = session.exec(
            select(Credential).where(Credential.service == service)
        ).first()
    if cred is None:
        raise AuthError(f"no credential stored for {service!r}; run `synapse auth google start`")

    now = datetime.now(tz=timezone.utc)
    expires_at = cred.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if (expires_at - now).total_seconds() < OAUTH_TOKEN_REFRESH_LEEWAY_SECONDS:
        logger.debug("refreshing {svc} access_token (about to expire)", svc=service)
        cred = _refresh_access_token(cred, http_client=http_client)

    return _decrypt(cred.access_token)


def credential_status(service: str) -> dict[str, Any]:
    """Return a redacted status dict suitable for `synapse auth status`."""
    with Session(get_engine()) as session:
        cred = session.exec(
            select(Credential).where(Credential.service == service)
        ).first()
    if cred is None:
        return {"service": service, "configured": False}
    return {
        "service": service,
        "configured": True,
        "expires_at": cred.expires_at.isoformat() if cred.expires_at else None,
        "has_refresh_token": bool(cred.refresh_token),
        "scopes": json.loads(cred.scopes or "[]"),
    }
