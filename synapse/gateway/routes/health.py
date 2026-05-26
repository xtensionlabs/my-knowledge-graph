"""`GET /health` — system health snapshot.

Returns enough information to debug whether captures are flowing without
exposing secrets. Used by the Telegram `/status` command and by external
monitors when the gateway is on a VPS (M6).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel
from sqlmodel import Session, select

from synapse import __version__
from synapse.capture.inbox import count_inbox_items
from synapse.config import get_settings
from synapse.graph.db import get_engine
from synapse.graph.models import InboxQueue

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Schema for the `/health` payload."""

    status: str
    version: str
    vault_path: str
    vault_initialized: bool
    db_exists: bool
    inbox_count: int
    pending_retries: int
    timestamp: str


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Return a snapshot of system health."""
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

    return HealthResponse(
        status="ok" if (vault_ok and db_ok) else "degraded",
        version=__version__,
        vault_path=str(settings.synapse_vault_path),
        vault_initialized=vault_ok,
        db_exists=db_ok,
        inbox_count=count_inbox_items() if vault_ok else 0,
        pending_retries=pending,
        timestamp=datetime.now(tz=timezone.utc).isoformat(),
    )
