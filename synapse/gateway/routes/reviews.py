"""`/reviews/*` — spaced-repetition review endpoints (PRD §8.2 `/review`)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from synapse.config import SYNTHESIZER_RETENTION_ALERTS
from synapse.graph.retention import apply_rating, get_due_reviews

router = APIRouter(prefix="/reviews", tags=["reviews"])


class DueCardOut(BaseModel):
    """One due review card."""

    node_id: str
    title: str
    application_question: str | None
    interval_days: float
    ease_factor: float
    review_count: int
    next_review: datetime


class DueListResponse(BaseModel):
    """`GET /reviews/due` response."""

    cards: list[DueCardOut]


@router.get("/due", response_model=DueListResponse)
async def list_due(
    limit: Annotated[int, Query(ge=1, le=50)] = SYNTHESIZER_RETENTION_ALERTS,
) -> DueListResponse:
    """Return CONCEPT cards whose `next_review` is on or before now."""
    due = get_due_reviews(limit=limit)
    return DueListResponse(
        cards=[
            DueCardOut(
                node_id=d.node_id,
                title=d.title,
                application_question=d.application_question,
                interval_days=d.interval_days,
                ease_factor=d.ease_factor,
                review_count=d.review_count,
                next_review=d.next_review,
            )
            for d in due
        ]
    )


class RatePayload(BaseModel):
    """`POST /reviews/{node_id}/rate` request."""

    quality: int = Field(ge=1, le=5)


class RateResponse(BaseModel):
    """`POST /reviews/{node_id}/rate` response."""

    node_id: str
    quality: int
    new_interval_days: float
    new_ease_factor: float
    new_review_count: int
    next_review: datetime


@router.post("/{node_id}/rate", response_model=RateResponse)
async def rate(node_id: str, payload: RatePayload) -> RateResponse:
    """Submit a 1-5 recall rating for a CONCEPT node."""
    try:
        state = apply_rating(node_id=node_id, quality=payload.quality)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RateResponse(
        node_id=node_id,
        quality=payload.quality,
        new_interval_days=state.interval_days,
        new_ease_factor=state.ease_factor,
        new_review_count=state.review_count,
        next_review=state.next_review,
    )
