"""SQLModel schemas for the Synapse knowledge graph and runtime state.

The seven node types and edge relations are defined in `SYNAPSE_PRD.md §5.3`.
Retention fields (SM-2) are populated only on CONCEPT nodes — left null for others.

This module deliberately stays free of business logic; it is the wire format.
CRUD lives in `synapse/graph/operations.py` (added in Milestone 1).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    """Naive-free UTC timestamp."""
    return datetime.now(tz=timezone.utc)


class NodeType(str, Enum):
    """The seven node types in the Synapse graph."""

    CONCEPT = "CONCEPT"
    FACT = "FACT"
    BUILD = "BUILD"
    PERSON = "PERSON"
    EVENT = "EVENT"
    QUESTION = "QUESTION"
    INSIGHT = "INSIGHT"


class RelationType(str, Enum):
    """Edge relation types. New types require an explicit FLAG."""

    REQUIRES = "requires"
    APPLIES_TO = "applies_to"
    CONTRADICTS = "contradicts"
    DERIVED_FROM = "derived_from"
    BRIDGES = "bridges"


# ── Graph schema ─────────────────────────────────────────────────────────────


class Node(SQLModel, table=True):
    """A node in the knowledge graph. Stored in SQLite; embedded in ChromaDB."""

    __tablename__ = "nodes"

    id: str = Field(primary_key=True)
    type: NodeType = Field(index=True)
    title: str = Field(index=True)
    content: str = ""
    source_ids: str = "[]"  # JSON array of capture ids
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    # SM-2 retention (CONCEPT only — null elsewhere)
    last_reviewed: Optional[datetime] = None
    next_review: Optional[datetime] = None
    interval_days: float = 1.0
    ease_factor: float = 2.5
    review_count: int = 0
    review_questions: str = "[]"  # JSON array, max 5 rotating

    # Startup mirror score (BUILD / CONCEPT)
    startup_relevance_score: float = 0.0

    # Metadata
    tags: str = "[]"
    embedding_id: str = ""
    obsidian_path: str = ""
    needs_review: bool = False  # set by Librarian when confidence < threshold


class Edge(SQLModel, table=True):
    """A directed, weighted edge between two nodes."""

    __tablename__ = "edges"

    id: str = Field(primary_key=True)
    source_node_id: str = Field(index=True, foreign_key="nodes.id")
    target_node_id: str = Field(index=True, foreign_key="nodes.id")
    relation_type: str = Field(index=True)  # validated against RelationType at write time
    weight: float = 1.0
    created_by: str = "user"  # librarian | synthesizer | user | strategist | guardian
    created_at: datetime = Field(default_factory=_utcnow)
    note: Optional[str] = None

    # Hebbian dynamics (PRD Appendix A.1). Null on legacy rows; defaults to
    # created_at on first strengthen so decay grace period is honored.
    last_strengthened: Optional[datetime] = None


# ── Runtime / gateway state ──────────────────────────────────────────────────


class Credential(SQLModel, table=True):
    """OAuth credentials. Tokens are Fernet-encrypted before write.

    Accessed only by `synapse/gateway/auth.py`. No other module should query
    this table directly.
    """

    __tablename__ = "credentials"

    id: str = Field(primary_key=True)
    service: str = Field(index=True)  # gmail | google_calendar | github
    access_token: str  # encrypted ciphertext
    refresh_token: str  # encrypted ciphertext
    expires_at: datetime
    scopes: str = "[]"  # JSON array
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class SessionState(SQLModel, table=True):
    """Singleton-ish row holding the current Foreground/Background/Horizon state.

    `id` is always "current"; `state_json` carries the full payload defined in
    PRD §6.1. Updated in place by the context layer.
    """

    __tablename__ = "session_state"

    id: str = Field(primary_key=True, default="current")
    state_json: str = "{}"
    energy_estimate: str = "medium"  # high | medium | low
    last_updated: datetime = Field(default_factory=_utcnow)


class FailedJob(SQLModel, table=True):
    """Outbound API calls that exhausted retry attempts. Manually retryable."""

    __tablename__ = "failed_jobs"

    id: str = Field(primary_key=True)
    service: str = Field(index=True)
    operation: str
    payload_json: str = "{}"
    error: str = ""
    created_at: datetime = Field(default_factory=_utcnow)
    last_retry_at: Optional[datetime] = None
    retry_count: int = 0


class InboxQueue(SQLModel, table=True):
    """Capture-channel retry queue.

    When a channel (Telegram, browser ext, etc.) fails to write to `inbox/`,
    the capture is enqueued here and replayed by a background worker. Zero
    capture loss is a hard requirement; this table is the safety net.
    """

    __tablename__ = "inbox_queue"

    id: str = Field(primary_key=True)
    source: str = Field(index=True)
    payload_json: str = "{}"  # the full capture envelope
    created_at: datetime = Field(default_factory=_utcnow)
    last_attempt_at: Optional[datetime] = None
    attempts: int = 0
    error: str = ""
    succeeded: bool = Field(default=False, index=True)


class CaptureLog(SQLModel, table=True):
    """Audit trail: every capture that successfully landed in `inbox/`.

    Used by `synapse status` and to detect drift between expected and actual
    capture counts during the success gate.
    """

    __tablename__ = "capture_log"

    id: str = Field(primary_key=True)
    source: str = Field(index=True)
    inbox_filename: str
    created_at: datetime = Field(default_factory=_utcnow, index=True)
    size_bytes: int = 0


class ApiUsage(SQLModel, table=True):
    """Per-call token usage + cost estimate for every Claude API request.

    Required by `CLAUDE.md` §"Handling Claude API Calls". Used by the daemon-
    facing `synapse status` to surface monthly spend and by tests to confirm
    every agent run produced exactly the expected number of calls.
    """

    __tablename__ = "api_usage"

    id: str = Field(primary_key=True)
    agent: str = Field(index=True)  # librarian | synthesizer | strategist | critic | scout | guardian
    model: str = Field(index=True)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    succeeded: bool = True
    error: str = ""
    created_at: datetime = Field(default_factory=_utcnow, index=True)
