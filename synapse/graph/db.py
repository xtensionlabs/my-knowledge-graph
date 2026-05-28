"""SQLite engine + session management for Synapse.

`get_engine()` returns the lazily-constructed engine bound to the configured
vault DB path. `init_db()` is called by `synapse init` to create all tables
AND apply any pending alembic migrations (idempotent — safe on fresh and
existing DBs alike).

Tests get an isolated engine via `make_engine(url)`.

Schema migrations are managed by alembic (`alembic/versions/*.py`). The CLAUDE.md
rule mandates every schema change goes through a migration file — never
modify the DB in place.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from loguru import logger
from sqlalchemy import event, inspect
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


def _alembic_config(db_url: str):  # type: ignore[no-untyped-def]
    """Build an alembic Config that reuses the project-level alembic.ini.

    We never let alembic read the URL from .ini — it always comes from
    `synapse.config.get_settings().db_url` (or an override via the `-x url=`
    CLI arg, handled in env.py).
    """
    from alembic.config import Config

    repo_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(repo_root / "alembic.ini"))
    # script_location is relative to alembic.ini, which lives at the repo root.
    cfg.set_main_option("script_location", str(repo_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def apply_migrations(engine: Engine | None = None) -> None:
    """Run `alembic upgrade head` against the target DB.

    Behavior:
    - Fresh DB: creates every table via the baseline migration.
    - Existing DB with no `alembic_version` table: detects the pre-alembic state,
      stamps it at the baseline revision, then upgrades to head. This is the
      one-time "import legacy DB" path.
    - DB already at head: no-op.

    Args:
        engine: Optional engine; defaults to the process-wide engine.
    """
    from alembic import command

    eng = engine or get_engine()
    cfg = _alembic_config(str(eng.url))

    inspector = inspect(eng)
    table_names = set(inspector.get_table_names())

    if "alembic_version" not in table_names and "nodes" in table_names:
        # Legacy DB created via SQLModel.metadata.create_all (pre-alembic).
        # Stamp it at the baseline so alembic recognizes the current state,
        # then proceed to upgrade to head (no-op if baseline IS head).
        logger.info("legacy DB detected at {url} — stamping at baseline", url=eng.url)
        command.stamp(cfg, "head")
        return

    command.upgrade(cfg, "head")


def init_db(engine: Engine | None = None) -> Path:
    """Create the database (via alembic) if needed and apply pending migrations.

    Idempotent. Safe to call on:
        - a brand-new vault (creates all tables from migrations)
        - an existing alembic-managed DB (applies any pending migrations)
        - a legacy DB created by `create_all` (stamps at baseline first)

    Args:
        engine: Optional engine override (used by tests).

    Returns:
        Absolute path to the SQLite file.
    """
    eng = engine or get_engine()
    settings = get_settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    apply_migrations(eng)
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
