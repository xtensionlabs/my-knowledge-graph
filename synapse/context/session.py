"""Session state — Foreground / Background / Horizon.

Per PRD §6.1 the session state is a JSON object stored as a single row in
SQLite. There is exactly one row, with `id="current"`. Reads/writes go through
this module; no other code touches the `session_state` table directly.

Foreground = what the user is actively working on now.
Background = adjacent items the system has surfaced for ambient awareness.
Horizon   = the next 72h of EVENTs the system needs to prep for.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from loguru import logger
from sqlmodel import Session

from synapse.graph.db import get_engine
from synapse.graph.models import SessionState

_SINGLETON_ID = "current"


@dataclass
class ForegroundState:
    """What the user is actively working on right now."""

    task: str = ""
    context_node_ids: list[str] = field(default_factory=list)
    started_at: str | None = None    # ISO-8601
    protect_until: str | None = None  # ISO-8601 — no surfacing allowed until this time


@dataclass
class BackgroundItem:
    """One ambient-awareness item the system surfaced but is not the focus."""

    description: str
    node_ids: list[str] = field(default_factory=list)
    surfaced_at: str = ""  # ISO-8601


@dataclass
class HorizonItem:
    """One upcoming EVENT the system is tracking for pre-loading."""

    event_node_id: str
    title: str
    date: str  # ISO-8601
    prep_concept_ids: list[str] = field(default_factory=list)
    preload_triggered: bool = False


@dataclass
class SessionSnapshot:
    """Full session-state view; serialized to/from the SessionState row."""

    foreground: ForegroundState = field(default_factory=ForegroundState)
    background: list[BackgroundItem] = field(default_factory=list)
    horizon: list[HorizonItem] = field(default_factory=list)
    energy_estimate: str = "medium"   # low | medium | high
    last_updated: str = ""             # ISO-8601

    def to_payload(self) -> dict[str, Any]:
        return {
            "foreground": self.foreground.__dict__,
            "background": [b.__dict__ for b in self.background],
            "horizon": [h.__dict__ for h in self.horizon],
            "energy_estimate": self.energy_estimate,
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> SessionSnapshot:
        fg = ForegroundState(**(payload.get("foreground") or {}))
        bg = [BackgroundItem(**b) for b in payload.get("background", [])]
        hz = [HorizonItem(**h) for h in payload.get("horizon", [])]
        return cls(
            foreground=fg,
            background=bg,
            horizon=hz,
            energy_estimate=payload.get("energy_estimate", "medium"),
            last_updated=payload.get("last_updated", ""),
        )


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def get_session() -> SessionSnapshot:
    """Return the current session snapshot; constructs an empty one on first call."""
    with Session(get_engine()) as db:
        row = db.get(SessionState, _SINGLETON_ID)
        if row is None:
            return SessionSnapshot(last_updated=_now_iso())
        try:
            payload = json.loads(row.state_json or "{}")
        except json.JSONDecodeError as exc:
            logger.warning("session_state JSON malformed; returning empty: {exc}", exc=exc)
            return SessionSnapshot(last_updated=_now_iso())
        snapshot = SessionSnapshot.from_payload(payload)
        snapshot.energy_estimate = row.energy_estimate or snapshot.energy_estimate
        snapshot.last_updated = row.last_updated.isoformat() if row.last_updated else snapshot.last_updated
        return snapshot


def save_session(snapshot: SessionSnapshot) -> None:
    """Persist the snapshot. Updates `last_updated`."""
    snapshot.last_updated = _now_iso()
    payload_json = json.dumps(snapshot.to_payload(), default=str)
    with Session(get_engine()) as db:
        row = db.get(SessionState, _SINGLETON_ID)
        if row is None:
            row = SessionState(
                id=_SINGLETON_ID,
                state_json=payload_json,
                energy_estimate=snapshot.energy_estimate,
                last_updated=datetime.now(tz=timezone.utc),
            )
        else:
            row.state_json = payload_json
            row.energy_estimate = snapshot.energy_estimate
            row.last_updated = datetime.now(tz=timezone.utc)
        db.add(row)
        db.commit()


def set_foreground(*, task: str, context_node_ids: list[str] | None = None) -> SessionSnapshot:
    """Convenience: replace the Foreground entirely."""
    snap = get_session()
    snap.foreground = ForegroundState(
        task=task,
        context_node_ids=context_node_ids or [],
        started_at=_now_iso(),
    )
    save_session(snap)
    return snap


def set_energy(estimate: str) -> SessionSnapshot:
    """Update the energy estimate and persist."""
    if estimate not in ("low", "medium", "high"):
        raise ValueError(f"energy must be low/medium/high, got {estimate!r}")
    snap = get_session()
    snap.energy_estimate = estimate
    save_session(snap)
    return snap
