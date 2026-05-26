"""SQLite engine + session management for Synapse.

`get_engine()` returns the lazily-constructed engine bound to the configured
vault DB path. `init_db()` is called by `synapse init` to create all tables.
Tests get an isolated engine via `make_engine(url)`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from loguru import logger
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

# Imported for side effect: registers tables with SQLModel.metadata.
from synapse.graph import models  # noqa: F401
from synapse.config import get_settings

_engine: Engine | None = None


def _enable_sqlite_wal(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
    """Turn on WAL mode + foreign keys on each new SQLite connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def make_engine(db_url: str, *, echo: bool = False) -> Engine:
    """Construct a SQLite engine with sane defaults.

    Args:
        db_url: SQLAlchemy URL, e.g. `sqlite:///path/to/synapse.db`.
        echo: If True, emit SQL to logs.

    Returns:
        A configured SQLAlchemy Engine instance.
    """
    engine = create_engine(
        db_url,
        echo=echo,
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _enable_sqlite_wal)
    return engine


def get_engine() -> Engine:
    """Return the process-wide engine, constructing on first call."""
    global _engine
    if _engine is None:
        settings = get_settings()
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        _engine = make_engine(settings.db_url)
    return _engine


def reset_engine() -> None:
    """Drop the cached engine reference. Test-only."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def init_db(engine: Engine | None = None) -> Path:
    """Create all tables. Idempotent.

    Args:
        engine: Optional engine override (used by tests). Defaults to the
            process-wide engine.

    Returns:
        Absolute path to the SQLite file.
    """
    eng = engine or get_engine()
    SQLModel.metadata.create_all(eng)
    settings = get_settings()
    logger.info("database initialized at {path}", path=settings.db_path)
    return settings.db_path


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """Context-managed SQLModel session with automatic commit/rollback.

    Yields:
        A live SQLModel `Session`.
    """
    eng = engine or get_engine()
    session = Session(eng)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
