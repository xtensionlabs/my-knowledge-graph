"""SM-2 algorithm tests — pure math, no I/O."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from synapse.graph.retention import (
    EASE_FLOOR,
    EASE_INITIAL,
    INTERVAL_INITIAL_DAYS,
    INTERVAL_SECOND_REVIEW_DAYS,
    initial_state,
    update_retention,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_initial_state_has_default_ease() -> None:
    s = initial_state(now=NOW)
    assert s.ease_factor == EASE_INITIAL
    assert s.interval_days == INTERVAL_INITIAL_DAYS
    assert s.review_count == 0
    assert s.last_reviewed == NOW


def test_first_perfect_review_uses_initial_interval() -> None:
    s = update_retention(
        quality=5,
        prior_ease=EASE_INITIAL,
        prior_interval_days=INTERVAL_INITIAL_DAYS,
        prior_review_count=0,
        now=NOW,
    )
    assert s.review_count == 1
    assert s.interval_days == INTERVAL_INITIAL_DAYS
    assert s.ease_factor >= EASE_INITIAL  # quality=5 never lowers ease


def test_second_review_jumps_to_six_days() -> None:
    s = update_retention(
        quality=4,
        prior_ease=EASE_INITIAL,
        prior_interval_days=INTERVAL_INITIAL_DAYS,
        prior_review_count=1,
        now=NOW,
    )
    assert s.review_count == 2
    assert s.interval_days == INTERVAL_SECOND_REVIEW_DAYS


def test_third_review_multiplies_by_ease() -> None:
    s = update_retention(
        quality=4,
        prior_ease=2.5,
        prior_interval_days=6.0,
        prior_review_count=2,
        now=NOW,
    )
    # quality=4 with EF=2.5: ease delta = 0.1 - 2*(0.08 + 2*0.02) = 0.1 - 0.24 = -0.14
    # new_ease = 2.5 - 0.14 = 2.36
    # interval = 6.0 * 2.36 = 14.16
    assert abs(s.ease_factor - 2.36) < 0.001
    assert abs(s.interval_days - 14.16) < 0.01


def test_blackout_resets_interval() -> None:
    s = update_retention(
        quality=1,
        prior_ease=2.5,
        prior_interval_days=30.0,
        prior_review_count=10,
        now=NOW,
    )
    assert s.interval_days == INTERVAL_INITIAL_DAYS
    # Ease still drops on bad recall.
    assert s.ease_factor < 2.5


def test_ease_never_below_floor() -> None:
    s = update_retention(
        quality=1,
        prior_ease=1.31,
        prior_interval_days=1.0,
        prior_review_count=0,
        now=NOW,
    )
    assert s.ease_factor >= EASE_FLOOR


def test_invalid_quality_raises() -> None:
    with pytest.raises(ValueError):
        update_retention(quality=0, prior_ease=2.5, prior_interval_days=1.0, prior_review_count=0)
    with pytest.raises(ValueError):
        update_retention(quality=6, prior_ease=2.5, prior_interval_days=1.0, prior_review_count=0)
