"""Energy inference — behavioral signal aggregation (PRD §6.2, Appendix A.3).

M2 shipped a deterministic stub returning "medium". M4 replaces it with a
real inference that blends three signals:

    1. Capture rate vs baseline — the strongest signal. High capture density
       compared to the 7-day rolling baseline implies active engagement.
    2. Time of day — local-time gating. Late-night hours default low unless
       capture rate overrides them.
    3. Day of week — weekdays bias slightly higher than weekends by default
       (configurable).

The output is one of "low" | "medium" | "high" and is persisted to
SessionState so callers (Synthesizer, Guardian, Strategist) can read it
synchronously without recomputing.

Design notes:
- We deliberately do NOT call Claude for this. Energy is a fast, cheap signal.
- We never use typing velocity from keystroke listeners (privacy + Windows
  hassle). Capture timestamps + their volume are a sufficient proxy.
- All thresholds are named constants in `synapse/config.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from loguru import logger
from sqlmodel import Session, func, select

from synapse.config import (
    ENERGY_BASELINE_WINDOW_HOURS,
    ENERGY_DAY_OF_WEEK_BOOST,
    ENERGY_HIGH_CAPTURE_RATE_MULT,
    ENERGY_LOW_CAPTURE_RATE_MULT,
    ENERGY_NIGHT_HOURS,
    ENERGY_RECENT_WINDOW_MINUTES,
    SYNAPSE_TIMEZONE,
    get_settings,
)
from synapse.context.session import get_session, set_energy
from synapse.graph.db import get_engine
from synapse.graph.models import CaptureLog

DEFAULT_ENERGY: str = "medium"
_VALID_LEVELS: tuple[str, ...] = ("low", "medium", "high")


@dataclass
class EnergySignals:
    """Snapshot of the inputs the inference saw — useful for tests + logging."""

    capture_rate_recent: float       # captures per hour, last ENERGY_RECENT_WINDOW_MINUTES
    capture_rate_baseline: float     # captures per hour, last ENERGY_BASELINE_WINDOW_HOURS
    capture_rate_ratio: float        # recent / baseline (1.0 if no baseline)
    is_night_hour: bool
    is_weekday: bool
    chosen: str


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _local_now() -> datetime:
    """Current time in the configured Synapse timezone."""
    try:
        tz = ZoneInfo(SYNAPSE_TIMEZONE)
    except Exception:  # noqa: BLE001 — bad zone string, fall back to UTC
        tz = timezone.utc
    return datetime.now(tz=tz)


def _count_captures_since(session: Session, since: datetime) -> int:
    """Count rows in capture_log with created_at >= since."""
    result = session.exec(
        select(func.count(CaptureLog.id)).where(CaptureLog.created_at >= since)
    ).one()
    # SQLModel may return a tuple-like or scalar depending on version.
    if isinstance(result, tuple):
        result = result[0]
    return int(result or 0)


def _is_night_hour(local: datetime) -> bool:
    """True if `local`'s hour falls in the configured night window."""
    start, end = ENERGY_NIGHT_HOURS
    h = local.hour
    if start <= end:
        return start <= h < end
    # Wraps midnight (e.g., 23..5)
    return h >= start or h < end


def gather_signals() -> EnergySignals:
    """Compute the energy signals + chosen level. Does NOT persist."""
    now_utc = _utcnow()
    recent_since = now_utc - timedelta(minutes=ENERGY_RECENT_WINDOW_MINUTES)
    baseline_since = now_utc - timedelta(hours=ENERGY_BASELINE_WINDOW_HOURS)

    with Session(get_engine()) as session:
        recent_count = _count_captures_since(session, recent_since)
        baseline_count = _count_captures_since(session, baseline_since)

    recent_hours = ENERGY_RECENT_WINDOW_MINUTES / 60.0
    baseline_hours = float(ENERGY_BASELINE_WINDOW_HOURS)
    recent_rate = recent_count / recent_hours if recent_hours > 0 else 0.0
    baseline_rate = baseline_count / baseline_hours if baseline_hours > 0 else 0.0

    if baseline_rate <= 0.0:
        # No baseline yet — treat ratio as 1.0 (neutral).
        ratio = 1.0
    else:
        ratio = recent_rate / baseline_rate

    local = _local_now()
    is_night = _is_night_hour(local)
    is_weekday = local.weekday() in ENERGY_DAY_OF_WEEK_BOOST

    chosen = _decide(ratio=ratio, is_night=is_night, is_weekday=is_weekday)

    return EnergySignals(
        capture_rate_recent=recent_rate,
        capture_rate_baseline=baseline_rate,
        capture_rate_ratio=ratio,
        is_night_hour=is_night,
        is_weekday=is_weekday,
        chosen=chosen,
    )


def _decide(*, ratio: float, is_night: bool, is_weekday: bool) -> str:
    """Pick low/medium/high from the inputs.

    Rules (deterministic, simple, override-friendly):
      - ratio ≥ HIGH_MULT → "high" (active burst beats time-of-day)
      - ratio ≤ LOW_MULT  → "low"
      - else if is_night and not is_weekday → "low" (rest periods)
      - else if is_night → "low" (weekday nights still low — sleep is sleep)
      - else if is_weekday → "medium"
      - else → "medium" (weekend default)
    """
    if ratio >= ENERGY_HIGH_CAPTURE_RATE_MULT:
        return "high"
    if ratio <= ENERGY_LOW_CAPTURE_RATE_MULT:
        return "low"
    if is_night:
        return "low"
    return "medium"


async def refresh_energy_estimate() -> str:
    """Recompute the energy estimate and persist to SessionState.

    Returns:
        The current energy estimate ("low" | "medium" | "high").
    """
    signals = gather_signals()
    snap = get_session()
    if snap.energy_estimate != signals.chosen:
        set_energy(signals.chosen)
    logger.debug(
        "energy estimate = {e} (ratio={r:.2f}, night={n}, weekday={w})",
        e=signals.chosen,
        r=signals.capture_rate_ratio,
        n=signals.is_night_hour,
        w=signals.is_weekday,
    )
    return signals.chosen


def current_energy() -> str:
    """Synchronous read of the persisted energy estimate."""
    return get_session().energy_estimate or DEFAULT_ENERGY
