"""Shared pytest fixtures.

Each test gets its own tmp vault + isolated SQLite DB. Settings cache is
reset before and after, so env-var-driven config can be safely mutated.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from synapse.config import reset_settings_cache
from synapse.graph import db as db_module


@pytest.fixture(autouse=True)
def isolated_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point Synapse at a fresh tmp vault for every test."""
    vault = tmp_path / "SYNAPSE"
    vault.mkdir(parents=True)
    monkeypatch.setenv("SYNAPSE_VAULT_PATH", str(vault))
    monkeypatch.setenv("SYNAPSE_SECRET_KEY", "test-not-a-real-key" + "x" * 24)
    monkeypatch.setenv("SYNAPSE_EMAIL_WEBHOOK_SECRET", "test-email-secret")
    monkeypatch.setenv("SYNAPSE_BROWSER_API_KEY", "test-browser-key")
    monkeypatch.setenv("SYNAPSE_LOG_LEVEL", "WARNING")
    # Critical: blank the live Telegram token so tests cannot connect to BotFather.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    reset_settings_cache()
    db_module.reset_engine()

    # Create the bare-minimum vault structure that inbox writes need.
    for sub in ("inbox", "archive", "attachments"):
        (vault / sub).mkdir(parents=True, exist_ok=True)
    (vault / ".synapse" / "run").mkdir(parents=True, exist_ok=True)
    (vault / ".synapse" / "logs").mkdir(parents=True, exist_ok=True)

    # Build DB tables.
    db_module.init_db()

    yield vault

    db_module.reset_engine()
    reset_settings_cache()


@pytest.fixture
def fastapi_app() -> Iterator:  # type: ignore[type-arg]
    """Build a fresh FastAPI app instance per test."""
    from synapse.gateway.main import create_app

    yield create_app()
