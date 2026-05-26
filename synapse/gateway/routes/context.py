"""`/context/*` — session state + horizon + EVENT management."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from synapse.context.horizon import add_event, refresh_horizon
from synapse.context.session import (
    SessionSnapshot,
    get_session,
    save_session,
    set_energy,
    set_foreground,
)

router = APIRouter(prefix="/context", tags=["context"])


class SessionResponse(BaseModel):
    """`GET /context/session` response."""

    state: dict


@router.get("/session", response_model=SessionResponse)
async def get_session_endpoint() -> SessionResponse:
    """Return the full session snapshot."""
    snap = get_session()
    return SessionResponse(state=snap.to_payload())


class ForegroundPayload(BaseModel):
    """`POST /context/foreground` request."""

    task: str = Field(min_length=1)
    context_node_ids: list[str] = Field(default_factory=list)


@router.post("/foreground", response_model=SessionResponse)
async def set_foreground_endpoint(payload: ForegroundPayload) -> SessionResponse:
    """Replace the current Foreground task."""
    snap = set_foreground(
        task=payload.task, context_node_ids=payload.context_node_ids
    )
    return SessionResponse(state=snap.to_payload())


class EnergyPayload(BaseModel):
    """`POST /context/energy` request."""

    estimate: str = Field(pattern="^(low|medium|high)$")


@router.post("/energy", response_model=SessionResponse)
async def set_energy_endpoint(payload: EnergyPayload) -> SessionResponse:
    """Manually override the energy estimate (also runs every 30 min via scheduler)."""
    snap = set_energy(payload.estimate)
    return SessionResponse(state=snap.to_payload())


class HorizonEventPayload(BaseModel):
    """`POST /context/horizon` request — manually add an EVENT to the horizon."""

    title: str = Field(min_length=1)
    date: datetime
    content: str = ""
    linked_concept_titles: list[str] = Field(default_factory=list)


class HorizonEventResponse(BaseModel):
    """Returned after creating a Horizon EVENT."""

    event_node_id: str
    title: str
    date: datetime
    horizon_size: int


@router.post("/horizon", response_model=HorizonEventResponse)
async def add_horizon_event(payload: HorizonEventPayload) -> HorizonEventResponse:
    """Create an EVENT and refresh the Horizon queue."""
    node = add_event(
        title=payload.title,
        date=payload.date,
        content=payload.content,
        linked_concept_titles=payload.linked_concept_titles,
    )
    return HorizonEventResponse(
        event_node_id=node.id,
        title=node.title,
        date=payload.date,
        horizon_size=refresh_horizon(),
    )
