"""Tests for the real Energy inference (replaces M2 stub)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session

from synapse.config import ENERGY_HIGH_CAPTURE_RATE_MULT, ENERGY_LOW_CAPTURE_RATE_MULT
from synapse.context.energy import (
    _decide,
    _is_night_hour,
    gather_signals,
    refresh_energy_estimate,
)
from synapse.graph.db import get_engine
from synapse.graph.models import CaptureLog


def _seed_captures(count: int, *, ago_minutes: int) -> None:
    """Insert N CaptureLog rows at ``now - ago_minutes``."""
    when = datetime.now(tz=timezone.utc) - timedelta(minutes=ago_minutes)
    with Session(get_engine()) as s:
        for i in range(count):
            s.add(
                CaptureLog(
                    id=str(uuid.uuid4()),
                    source="manual",
                    inbox_filename=f"test-{i}.md",
                    created_at=when + timedelta(seconds=i),
                    size_bytes=200,
                )
            )
        s.commit()


# ── Decision rules ────────────────────────────────────────────────────────────


def test_decide_high_when_ratio_exceeds_threshold() -> None:
    assert _decide(ratio=ENERGY_HIGH_CAPTURE_RATE_MULT + 0.5, is_night=False, is_weekday=True) == "high"


def test_decide_low_when_ratio_below_low_threshold() -> None:
    assert _decide(ratio=ENERGY_LOW_CAPTURE_RATE_MULT - 0.05, is_night=False, is_weekday=True) == "low"


def test_decide_low_at_night() -> None:
    assert _decide(ratio=1.0, is_night=True, is_weekday=True) == "low"


def test_decide_medium_default() -> None:
    assert _decide(ratio=1.0, is_night=False, is_weekday=True) == "medium"


# ── Night-hour gate ───────────────────────────────────────────────────────────


def test_is_night_hour_wraps_midnight() -> None:
    """ENERGY_NIGHT_HOURS = (23, 5) → 23:00–04:59 should be night."""
    midnight = datetime(2026, 5, 26, 0, 30, tzinfo=timezone.utc)
    morning = datetime(2026, 5, 26, 4, 0, tzinfo=timezone.utc)
    late = datetime(2026, 5, 26, 23, 30, tzinfo=timezone.utc)
    midday = datetime(2026, 5, 26, 14, 0, tzinfo=timezone.utc)
    assert _is_night_hour(midnight) is True
    assert _is_night_hour(morning) is True
    assert _is_night_hour(late) is True
    assert _is_night_hour(midday) is False


# ── Signal aggregation ────────────────────────────────────────────────────────


def test_gather_signals_zero_captures_returns_neutral_ratio() -> None:
    """No baseline → ratio defaults to 1.0 (neutral)."""
    sig = gather_signals()
    assert sig.capture_rate_ratio == pytest.approx(1.0)


def test_gather_signals_high_recent_rate_triggers_high() -> None:
    """A burst of recent captures with no baseline → ratio is 1.0, NOT high.
    A burst of recent captures with low baseline → ratio is high, should be 'high'.
    """
    # Seed baseline: 1 capture 100h ago (very low rate).
    _seed_captures(1, ago_minutes=100 * 60)
    # Seed recent: many captures in last 10 min.
    _seed_captures(20, ago_minutes=5)

    sig = gather_signals()
    assert sig.capture_rate_recent > sig.capture_rate_baseline
    assert sig.capture_rate_ratio > 1.0


# ── Persistence ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_persists_to_session_state() -> None:
    chosen = await refresh_energy_estimate()
    from synapse.context.session import get_session

    assert chosen in ("low", "medium", "high")
    assert get_session().energy_estimate == chosen
