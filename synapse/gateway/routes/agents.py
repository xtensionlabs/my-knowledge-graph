"""`/agents/*` — agent invocation endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from synapse.agents.librarian import librarian
from synapse.config import LIBRARIAN_MAX_ITEMS_PER_RUN

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentResultOut(BaseModel):
    """Schema mirroring `agents.base.AgentResult`."""

    agent: str
    ok: bool
    summary: str
    artifacts: dict[str, Any]
    errors: list[str]


@router.post("/librarian/run", response_model=AgentResultOut)
async def librarian_run(
    max_items: Annotated[int, Query(ge=1, le=500)] = LIBRARIAN_MAX_ITEMS_PER_RUN,
) -> AgentResultOut:
    """Trigger the Librarian on up to `max_items` inbox files."""
    try:
        result = await librarian.run(max_items=max_items)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return AgentResultOut(
        agent=result.agent,
        ok=result.ok,
        summary=result.summary,
        artifacts=result.artifacts,
        errors=result.errors,
    )


@router.post("/synthesizer/run", response_model=AgentResultOut)
async def synthesizer_run() -> AgentResultOut:
    """Trigger the Synthesizer on demand. Returns the rendered Delta Briefing."""
    from synapse.agents.synthesizer import synthesizer

    try:
        result = await synthesizer.run()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return AgentResultOut(
        agent=result.agent,
        ok=result.ok,
        summary=result.summary,
        artifacts=result.artifacts,
        errors=result.errors,
    )
