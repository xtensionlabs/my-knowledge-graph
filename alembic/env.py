"""Alembic environment — wires alembic to Synapse's SQLModel metadata.

Synapse never hardcodes the DB URL in alembic.ini. We pull it from
`get_settings().db_url` at runtime so migrations always target the live
vault DB (or, in tests, the conftest-provisioned tmp vault).
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlmodel import SQLModel

# Importing the models package registers every SQLModel table on
# `SQLModel.metadata`. Without this import alembic autogenerate sees nothing.
from synapse.config import get_settings
from synapse.graph import db as _db  # noqa: F401 — uses make_engine for offline-safe pragmas
from synapse.graph import models  # noqa: F401 — side-effect: registers tables


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def _db_url() -> str:
    """Pull the live DB URL from Synapse settings (vault-aware)."""
    # Allow ad-hoc overrides via `alembic -x url=sqlite:///path.db upgrade head`
    x_args = context.get_x_argument(as_dictionary=True)
    if "url" in x_args:
        return x_args["url"]
    return get_settings().db_url


def run_migrations_offline() -> None:
    """Generate SQL without a live DB connection (rare; here for completeness)."""
    context.configure(
        url=_db_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite-friendly ALTER TABLE
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the live engine (the normal path)."""
    engine = _db.make_engine(_db_url())
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite-friendly ALTER TABLE
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
