"""Timezone-aware datetime helpers used across the codebase.

SQLite strips tzinfo on read, so `assume_utc` rehydrates naive datetimes as
UTC. `utcnow` is the project-wide source of "now" — every module that needs
the current time should import this, not call `datetime.now()` directly.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(tz=timezone.utc)


def assume_utc(dt: datetime | None) -> datetime | None:
    """Stamp a naive datetime as UTC; pass-through for tz-aware or None."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
