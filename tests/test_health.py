"""Smoke test the /health endpoint."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_health_returns_ok(fastapi_app) -> None:  # type: ignore[no-untyped-def]
    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["vault_initialized"] is True
    assert body["db_exists"] is True
    assert body["inbox_count"] == 0
    assert body["pending_retries"] == 0
    assert "version" in body
    assert "uptime_seconds" in body and body["uptime_seconds"] >= 0


@pytest.mark.asyncio
async def test_health_returns_503_when_vault_missing(
    fastapi_app, monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    """Monitor-friendly: degraded state must surface as HTTP 503, not 200."""
    from synapse.config import reset_settings_cache

    monkeypatch.setenv("SYNAPSE_VAULT_PATH", str(tmp_path / "does-not-exist"))
    reset_settings_cache()
    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.get("/health")
    assert resp.status_code == 503
    assert resp.json()["status"] == "degraded"
