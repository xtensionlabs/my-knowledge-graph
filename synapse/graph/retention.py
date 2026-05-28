"""SM-2 spaced-repetition algorithm + review API.

Pure function `update_retention` over (quality, prior_ease, prior_interval, prior_review_count).
The DB-touching `get_due_reviews` / `apply_rating` functions sit alongside it
so callers (Synthesizer, Telegram bot, dashboard) have one import.

Reference: SuperMemo SM-2 (Wozniak 1990). Quality ratings are 0-5 in the
original spec; Synapse uses 1-5 to match the dashboard buttons and treats
1 as "blackout, reset interval".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlmodel import Session, asc, select

from synapse.config import SYNTHESIZER_QUESTION_BANK_MAX
from synapse.graph.db import get_engine
from synapse.graph.models import Node, NodeType
from synapse.utils.time import assume_utc as _assume_utc, utcnow as _utcnow

# SM-2 constants — never edit casually; the algorithm is calibrated on these values.
EASE_FLOOR: float = 1.3
EASE_INITIAL: float = 2.5
INTERVAL_INITIAL_DAYS: float = 1.0
INTERVAL_SECOND_REVIEW_DAYS: float = 6.0


@dataclass(frozen=True)
class RetentionState:
    """Immutable retention snapshot used as input and output of `update_retention`."""

    interval_days: float
    ease_factor: float
    review_count: int
    next_review: datetime
    last_reviewed: datetime




def update_retention(
    *,
    quality: int,
    prior_ease: float,
    prior_interval_days: float,
    prior_review_count: int,
    now: datetime | None = None,
) -> RetentionState:
    """Apply one SM-2 review tick.

    Args:
        quality: User self-rating, 1 (blackout) to 5 (perfect). Anything <3 forces
            an interval reset to 1 day.
        prior_ease: Current ease factor (≥ EASE_FLOOR).
        prior_interval_days: Days between the last two reviews.
        prior_review_count: How many times this concept has been reviewed.
        now: Override for the "current time" (test injection).

    Returns:
        A new `RetentionState` reflecting the updated SM-2 fields.

    Raises:
        ValueError: If `quality` is out of range 1..5.
    """
    if not 1 <= quality <= 5:
        raise ValueError(f"quality must be in 1..5, got {quality}")

    now = now or _utcnow()
    new_count = prior_review_count + 1

    # SM-2 ease update: EF' = EF + (0.1 - (5-q)*(0.08 + (5-q)*0.02))
    # Adapted for 1-5 scale: we map quality -> SM-2 q via q = quality - 1.
    q = quality - 1  # now 0..4
    delta = 0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)
    new_ease = max(EASE_FLOOR, prior_ease + delta)

    # Interval update.
    if quality < 3:
        # Reset on poor recall — interval back to 1 day, but preserve ease decline.
        new_interval = INTERVAL_INITIAL_DAYS
    elif new_count == 1:
        new_interval = INTERVAL_INITIAL_DAYS
    elif new_count == 2:
        new_interval = INTERVAL_SECOND_REVIEW_DAYS
    else:
        new_interval = round(prior_interval_days * new_ease, 4)

    next_review = now + timedelta(days=new_interval)
    return RetentionState(
        interval_days=new_interval,
        ease_factor=new_ease,
        review_count=new_count,
        next_review=next_review,
        last_reviewed=now,
    )


def initial_state(now: datetime | None = None) -> RetentionState:
    """Return the retention state for a freshly-created CONCEPT node.

    M1 sets these on every new CONCEPT created by the Librarian so that M2's
    review queue has a sensible `next_review` to operate on.
    """
    now = now or _utcnow()
    return RetentionState(
        interval_days=INTERVAL_INITIAL_DAYS,
        ease_factor=EASE_INITIAL,
        review_count=0,
        next_review=now + timedelta(days=INTERVAL_INITIAL_DAYS),
        last_reviewed=now,
    )


# ── Review API (DB-backed) ───────────────────────────────────────────────────


@dataclass
class DueReview:
    """A CONCEPT node that is due for review, with the latest application question."""

    node_id: str
    title: str
    content: str
    application_question: str | None  # most-recent rotated question, if any
    interval_days: float
    ease_factor: float
    review_count: int
    next_review: datetime


def get_due_reviews(*, limit: int = 5, now: datetime | None = None) -> list[DueReview]:
    """Return the CONCEPTs whose `next_review` is on or before `now`.

    Ordered by most-overdue first.

    Args:
        limit: Max number of cards to return.
        now: Override "current time" for tests.

    Returns:
        List of `DueReview` records.
    """
    now = now or _utcnow()
    out: list[DueReview] = []
    with Session(get_engine()) as db:
        stmt = (
            select(Node)
            .where(Node.type == NodeType.CONCEPT)
            .where(Node.next_review != None)  # noqa: E711
            .where(Node.next_review <= now)
            .order_by(asc(Node.next_review))
        )
        rows = list(db.exec(stmt).all())
    for node in rows[:limit]:
        try:
            bank = json.loads(node.review_questions or "[]")
        except json.JSONDecodeError:
            bank = []
        question = bank[-1] if bank else None
        out.append(
            DueReview(
                node_id=node.id,
                title=node.title,
                content=node.content,
                application_question=question,
                interval_days=node.interval_days,
                ease_factor=node.ease_factor,
                review_count=node.review_count,
                next_review=_assume_utc(node.next_review),  # type: ignore[arg-type]
            )
        )
    return out


def apply_rating(
    *, node_id: str, quality: int, now: datetime | None = None
) -> RetentionState:
    """Apply a 1-5 SM-2 rating to a CONCEPT node and persist the new state.

    Args:
        node_id: Target CONCEPT's id.
        quality: 1 (blackout) … 5 (perfect).
        now: Override for tests.

    Returns:
        The new RetentionState (also persisted on the row).

    Raises:
        ValueError: If quality is out of range or the node isn't a CONCEPT.
    """
    if not 1 <= quality <= 5:
        raise ValueError(f"quality must be in 1..5, got {quality}")
    now = now or _utcnow()
    with Session(get_engine()) as db:
        node = db.get(Node, node_id)
        if node is None:
            raise ValueError(f"node {node_id!r} not found")
        if node.type != NodeType.CONCEPT:
            raise ValueError(f"only CONCEPT nodes carry retention state (got {node.type})")
        state = update_retention(
            quality=quality,
            prior_ease=node.ease_factor,
            prior_interval_days=node.interval_days,
            prior_review_count=node.review_count,
            now=now,
        )
        node.ease_factor = state.ease_factor
        node.interval_days = state.interval_days
        node.review_count = state.review_count
        node.last_reviewed = state.last_reviewed
        node.next_review = state.next_review
        db.add(node)
        db.commit()
    logger.info(
        "review rated: node={id} quality={q} → ef={ef:.2f} interval={iv:.2f}d",
        id=node_id, q=quality, ef=state.ease_factor, iv=state.interval_days,
    )
    return state


def push_question(node_id: str, question: str) -> int:
    """Add an application question to a CONCEPT's rotating bank (max size enforced).

    Args:
        node_id: Target CONCEPT.
        question: The new question text.

    Returns:
        The number of questions now in the bank.
    """
    with Session(get_engine()) as db:
        node = db.get(Node, node_id)
        if node is None or node.type != NodeType.CONCEPT:
            return 0
        try:
            bank: list[str] = json.loads(node.review_questions or "[]")
        except json.JSONDecodeError:
            bank = []
        question = question.strip()
        if not question:
            return len(bank)
        # Drop dup of last to avoid trivial repeats.
        if bank and bank[-1].strip() == question:
            return len(bank)
        bank.append(question)
        bank = bank[-SYNTHESIZER_QUESTION_BANK_MAX:]
        node.review_questions = json.dumps(bank)
        db.add(node)
        db.commit()
        return len(bank)
