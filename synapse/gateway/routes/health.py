"""`GET /health` — system health snapshot.

Returns enough information to debug whether captures are flowing without
exposing secrets. Used by the Telegram `/status` command and by external
monitors (UptimeRobot, Better Stack) when the gateway is on a VPS.

When the gateway is degraded (missing vault, missing DB, or unreadable DB)
the endpoint returns HTTP 503 so external monitors can trigger on the default
HTTP-status check — no need to teach the monitor to parse the JSON body.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlmodel import Session, select

from synapse import __version__
from synapse.capture.inbox import count_inbox_items
from synapse.config import get_settings
from synapse.graph.db import get_engine
from synapse.graph.models import InboxQueue
from synapse.utils.time import utcnow

router = APIRouter(tags=["health"])

# Process start time — used to compute uptime_seconds so monitors can detect
# restart loops (uptime jumps back to ~0 every minute = something is crashing).
_PROCESS_START_MONOTONIC = time.monotonic()


class HealthResponse(BaseModel):
    """Schema for the `/health` payload."""

    status: str
    version: str
    vault_path: str
    vault_initialized: bool
    db_exists: bool
    inbox_count: int
    pending_retries: int
    uptime_seconds: int
    timestamp: str


@router.get("/health", response_model=HealthResponse)
async def health(response: Response) -> HealthResponse:
    """Return a snapshot of system health.

    Returns HTTP 200 when healthy, HTTP 503 when degraded.
    """
    settings = get_settings()
    vault_ok = settings.synapse_vault_path.exists()
    db_ok = settings.db_path.exists()

    pending = 0
    if db_ok:
        try:
            with Session(get_engine()) as session:
                pending = len(
                    session.exec(
                        select(InboxQueue).where(InboxQueue.succeeded == False)  # noqa: E712
                    ).all()
                )
        except Exception:  # noqa: BLE001
            pending = -1  # DB present but unreadable — surface visibly

    healthy = vault_ok and db_ok and pending >= 0
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status="ok" if healthy else "degraded",
        version=__version__,
        vault_path=str(settings.synapse_vault_path),
        vault_initialized=vault_ok,
        db_exists=db_ok,
        inbox_count=count_inbox_items() if vault_ok else 0,
        pending_retries=pending,
        uptime_seconds=int(time.monotonic() - _PROCESS_START_MONOTONIC),
        timestamp=utcnow().isoformat(),
    )
