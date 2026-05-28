"""OAuth gateway routes — start + callback for external providers.

Endpoints:

    GET  /auth/google/start     — returns the Google authorize URL + state
    GET  /auth/google/callback  — Google redirects here with ?code=&state=
    GET  /auth/{service}/status — show whether a credential is stored

Only the gateway sees raw tokens. The callback delegates immediately to
`synapse.gateway.auth.complete_authorization()` which encrypts and persists.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from synapse.gateway.auth import (
    AuthError,
    complete_authorization,
    credential_status,
    start_authorization,
)

router = APIRouter()


@router.get("/google/start")
def auth_google_start() -> dict[str, Any]:
    """Generate the Google OAuth authorize URL.

    The CLI (`synapse auth google start`) opens this URL in a browser.
    """
    try:
        result = start_authorization("google_calendar")
    except AuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"authorize_url": result.authorize_url, "state": result.state}


@router.get("/google/callback", response_class=HTMLResponse)
def auth_google_callback(
    code: str = Query(..., description="Authorization code from Google"),
    state: str = Query(..., description="CSRF state echoed by Google"),
) -> str:
    """Google redirects here after the user grants consent.

    Exchanges code for tokens, stores encrypted, returns a tiny HTML page
    the user can close.
    """
    try:
        complete_authorization(service="google_calendar", code=code, state=state)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return (
        "<!doctype html><html><head><title>Synapse — connected</title></head>"
        "<body style='font-family:monospace;padding:2em'>"
        "<h2>✓ Google Calendar connected to Synapse</h2>"
        "<p>You can close this tab.</p></body></html>"
    )


@router.get("/{service}/status")
def auth_status(service: str) -> dict[str, Any]:
    """Show whether a credential is stored for `service` (no token values)."""
    return credential_status(service)
