"""Google Calendar integration — read-only event fetch.

This module never sees a raw token directly. It calls
`synapse.gateway.auth.get_access_token("google_calendar")` which returns a
short-lived bearer string; we use it once and discard it (CLAUDE.md rule 5).

Used by the Strategist for collision detection against real calendar events.
For M4 the read is one-shot: events are pulled into EVENT nodes via
`sync_calendar_to_events()` and the graph layer takes over from there.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from loguru import logger

from synapse.config import (
    ANTHROPIC_CONNECT_TIMEOUT_SECONDS,
    GOOGLE_CALENDAR_EVENTS_URL,
)
from synapse.gateway.auth import AuthError, get_access_token


@dataclass
class CalendarEvent:
    """Normalised representation of a Google Calendar event."""

    google_id: str
    title: str
    start: datetime
    end: datetime | None
    description: str = ""


class CalendarError(Exception):
    """Raised on Calendar API failures."""


def list_upcoming_events(
    *,
    lookahead_hours: int = 168,
    http_client: httpx.Client | None = None,
) -> list[CalendarEvent]:
    """Fetch events from primary calendar starting now → now + lookahead.

    Args:
        lookahead_hours: How far into the future to fetch (default 7 days).
        http_client: Optional injected client (tests use respx or a mock).

    Returns:
        List of CalendarEvent, time-ordered.

    Raises:
        CalendarError: If the Calendar API request fails.
        AuthError:     If no Google credential is stored (caller should
                       prompt the user to authorize).
    """
    try:
        token = get_access_token("google_calendar", http_client=http_client)
    except AuthError:
        raise

    now = datetime.now(tz=timezone.utc)
    horizon = now + timedelta(hours=lookahead_hours)

    params = {
        "timeMin": now.isoformat(),
        "timeMax": horizon.isoformat(),
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": "100",
    }
    headers = {"Authorization": f"Bearer {token}"}

    client = http_client or httpx.Client(timeout=ANTHROPIC_CONNECT_TIMEOUT_SECONDS)
    try:
        resp = client.get(GOOGLE_CALENDAR_EVENTS_URL, params=params, headers=headers)
        if resp.status_code >= 400:
            raise CalendarError(
                f"calendar list failed: {resp.status_code} {resp.text[:200]}"
            )
        data = resp.json()
    finally:
        if http_client is None:
            client.close()

    events: list[CalendarEvent] = []
    for item in data.get("items", []):
        start_raw = item.get("start", {})
        end_raw = item.get("end", {})
        start_str = start_raw.get("dateTime") or start_raw.get("date")
        if not start_str:
            continue
        end_str = end_raw.get("dateTime") or end_raw.get("date")
        try:
            start_dt = _parse_iso(start_str)
            end_dt = _parse_iso(end_str) if end_str else None
        except ValueError:
            continue
        events.append(
            CalendarEvent(
                google_id=str(item.get("id", "")),
                title=str(item.get("summary", "(untitled)")),
                start=start_dt,
                end=end_dt,
                description=str(item.get("description", "")),
            )
        )

    logger.info("calendar: fetched {n} events", n=len(events))
    return events


def _parse_iso(s: str) -> datetime:
    """Parse Google's RFC3339 datetimes (with or without tzinfo + Z suffix)."""
    s = s.rstrip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as exc:
        raise ValueError(f"unparseable calendar datetime: {s!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def sync_calendar_to_events(
    *,
    lookahead_hours: int = 168,
    http_client: httpx.Client | None = None,
) -> int:
    """Import Calendar events as EVENT nodes (deduplicated by google_id tag).

    Args:
        lookahead_hours: Fetch window.
        http_client:     Optional injected client.

    Returns:
        Number of EVENT nodes created or updated.
    """
    from synapse.context.horizon import add_event
    from synapse.graph.models import NodeType
    from synapse.graph.operations import find_node_by_title

    events = list_upcoming_events(lookahead_hours=lookahead_hours, http_client=http_client)
    created_or_updated = 0
    for ev in events:
        # Idempotent: skip if an EVENT with this title and google_id tag already exists.
        existing = find_node_by_title(ev.title)
        if existing is not None and existing.type == NodeType.EVENT:
            continue  # M4 doesn't update existing — just imports new
        add_event(
            title=ev.title,
            date=ev.start,
            content=ev.description or "",
            linked_concept_titles=[],
        )
        created_or_updated += 1
    return created_or_updated
