"""OAuth tests — token encryption + mocked Google flow."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from synapse.gateway.auth import (
    AuthError,
    _decrypt,
    _encrypt,
    complete_authorization,
    credential_status,
    get_access_token,
    start_authorization,
)


@pytest.fixture(autouse=True)
def _set_oauth_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests need both Google + GitHub OAuth client credentials present."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-google-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-google-secret")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "Iv1.test-github-client-id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "test-github-client-secret")
    # Force re-cache so new env vars are picked up.
    from synapse.config import reset_settings_cache
    reset_settings_cache()


# ── Encryption round-trip ─────────────────────────────────────────────────────


def test_encrypt_decrypt_roundtrip() -> None:
    plaintext = "ya29.a0AfH6SMA-fake-access-token"
    ciphertext = _encrypt(plaintext)
    assert ciphertext != plaintext
    assert _decrypt(ciphertext) == plaintext


def test_decrypt_with_wrong_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    ciphertext = _encrypt("secret")
    monkeypatch.setenv("SYNAPSE_SECRET_KEY", "completely-different-key" + "y" * 24)
    from synapse.config import reset_settings_cache

    reset_settings_cache()
    with pytest.raises(AuthError):
        _decrypt(ciphertext)


# ── Authorization start ───────────────────────────────────────────────────────


def test_start_authorization_returns_google_url() -> None:
    result = start_authorization("google_calendar")
    assert result.authorize_url.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert "client_id=" in result.authorize_url
    assert "state=" in result.authorize_url
    assert "scope=" in result.authorize_url
    assert len(result.state) >= 16


def test_start_authorization_without_client_id_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    from synapse.config import reset_settings_cache

    reset_settings_cache()
    with pytest.raises(AuthError, match="GOOGLE_CLIENT_ID"):
        start_authorization("google_calendar")


# ── Callback / token exchange (mocked) ────────────────────────────────────────


def _mock_token_response(payload: dict[str, Any], status: int = 200) -> httpx.Client:
    """Return an httpx.Client whose .post returns a fake response."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = status
    response.json = lambda: payload
    response.text = str(payload)

    client = MagicMock(spec=httpx.Client)
    client.post = MagicMock(return_value=response)
    client.get = MagicMock(return_value=response)
    return client  # type: ignore[no-any-return]


def test_complete_authorization_stores_encrypted_tokens() -> None:
    auth = start_authorization("google_calendar")
    fake = _mock_token_response(
        {
            "access_token": "ya29.fake-access",
            "refresh_token": "1//fake-refresh",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/calendar.readonly",
            "token_type": "Bearer",
        }
    )
    cred = complete_authorization(
        service="google_calendar", code="fake-code", state=auth.state, http_client=fake
    )
    assert cred.service == "google_calendar"
    assert cred.access_token != "ya29.fake-access"  # encrypted
    assert cred.refresh_token != "1//fake-refresh"  # encrypted

    # Round-trip: get_access_token should decrypt back to plaintext.
    plain = get_access_token("google_calendar", http_client=fake)
    assert plain == "ya29.fake-access"


def test_complete_authorization_rejects_bad_state() -> None:
    start_authorization("google_calendar")  # generates real state
    fake = _mock_token_response({"access_token": "x", "expires_in": 3600})
    with pytest.raises(AuthError, match="state"):
        complete_authorization(
            service="google_calendar", code="c", state="wrong-state", http_client=fake
        )


def test_complete_authorization_propagates_exchange_failure() -> None:
    auth = start_authorization("google_calendar")
    fake = _mock_token_response({"error": "invalid_grant"}, status=400)
    with pytest.raises(AuthError, match="token exchange failed"):
        complete_authorization(
            service="google_calendar", code="c", state=auth.state, http_client=fake
        )


# ── Refresh flow ──────────────────────────────────────────────────────────────


def test_get_access_token_refreshes_when_near_expiry() -> None:
    auth = start_authorization("google_calendar")
    fake = _mock_token_response(
        {
            "access_token": "first-access",
            "refresh_token": "stable-refresh",
            "expires_in": 1,  # nearly expired immediately
            "scope": "https://www.googleapis.com/auth/calendar.readonly",
        }
    )
    complete_authorization(
        service="google_calendar", code="c", state=auth.state, http_client=fake
    )

    # Second token response — the refresh.
    refresh_fake = _mock_token_response(
        {"access_token": "second-access", "expires_in": 3600}
    )
    plain = get_access_token("google_calendar", http_client=refresh_fake)
    assert plain == "second-access"


# ── Status report ─────────────────────────────────────────────────────────────


def test_credential_status_when_unconfigured() -> None:
    status = credential_status("google_calendar")
    assert status["configured"] is False


def test_credential_status_when_configured() -> None:
    auth = start_authorization("google_calendar")
    fake = _mock_token_response(
        {
            "access_token": "abc",
            "refresh_token": "def",
            "expires_in": 3600,
            "scope": "scopeA scopeB",
        }
    )
    complete_authorization(
        service="google_calendar", code="c", state=auth.state, http_client=fake
    )
    status = credential_status("google_calendar")
    assert status["configured"] is True
    assert status["has_refresh_token"] is True
    assert "scopeA" in status["scopes"]


# ── GitHub flow ─────────────────────────────────────────────────────────────


from synapse.gateway.auth import (  # noqa: E402 — keep GitHub imports near their tests
    complete_github_authorization,
    start_github_authorization,
)


def test_github_start_returns_valid_authorize_url() -> None:
    result = start_github_authorization()
    assert result.authorize_url.startswith("https://github.com/login/oauth/authorize")
    assert "client_id=Iv1.test-github-client-id" in result.authorize_url
    assert "scope=repo" in result.authorize_url
    assert "state=" in result.authorize_url
    assert len(result.state) >= 16


def test_github_start_without_client_id_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_CLIENT_ID", "")
    from synapse.config import reset_settings_cache

    reset_settings_cache()
    with pytest.raises(AuthError, match="GITHUB_CLIENT_ID"):
        start_github_authorization()


def test_github_complete_stores_encrypted_token() -> None:
    auth = start_github_authorization()
    fake = _mock_token_response(
        {
            "access_token": "gho_fake-github-access-token",
            "scope": "repo,read:user",
            "token_type": "bearer",
        }
    )
    cred = complete_github_authorization(code="fake-code", state=auth.state, http_client=fake)
    assert cred.service == "github"
    assert cred.access_token != "gho_fake-github-access-token"  # encrypted
    assert cred.refresh_token == ""  # GitHub OAuth Apps don't issue refresh tokens

    # Round-trip: get_access_token decrypts back to plaintext, no refresh attempted.
    plain = get_access_token("github", http_client=fake)
    assert plain == "gho_fake-github-access-token"


def test_github_complete_rejects_bad_state() -> None:
    start_github_authorization()
    fake = _mock_token_response({"access_token": "x"})
    with pytest.raises(AuthError, match="state"):
        complete_github_authorization(code="c", state="wrong-state", http_client=fake)


def test_github_complete_surfaces_error_field_in_response() -> None:
    """GitHub returns HTTP 200 even on failure — the body has {"error": "..."}.
    Our code must detect that and raise AuthError."""
    auth = start_github_authorization()
    fake = _mock_token_response(
        {"error": "bad_verification_code", "error_description": "code expired"}
    )
    with pytest.raises(AuthError, match="bad_verification_code"):
        complete_github_authorization(code="c", state=auth.state, http_client=fake)


def test_github_status_when_configured() -> None:
    auth = start_github_authorization()
    fake = _mock_token_response(
        {"access_token": "gho_x", "scope": "repo,read:user"}
    )
    complete_github_authorization(code="c", state=auth.state, http_client=fake)
    status = credential_status("github")
    assert status["configured"] is True
    assert "repo" in status["scopes"]
    assert "read:user" in status["scopes"]
    # GitHub doesn't issue refresh tokens — has_refresh_token should be False.
    assert status["has_refresh_token"] is False


def test_github_scopes_comma_separated_serialization() -> None:
    """GitHub returns scopes comma-separated (not space-separated like Google)."""
    auth = start_github_authorization()
    fake = _mock_token_response(
        {"access_token": "gho_x", "scope": "repo,read:user,user:email"}
    )
    cred = complete_github_authorization(code="c", state=auth.state, http_client=fake)
    import json as _json
    scopes = _json.loads(cred.scopes)
    assert scopes == ["repo", "read:user", "user:email"]


def test_redirect_uri_uses_public_url_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """In production, OAuth callbacks must use SYNAPSE_PUBLIC_URL, not host:port."""
    from synapse.config import reset_settings_cache
    from synapse.gateway.auth import _redirect_uri

    monkeypatch.setenv("SYNAPSE_PUBLIC_URL", "https://synapse.example.com")
    reset_settings_cache()
    assert _redirect_uri("/auth/github/callback") == "https://synapse.example.com/auth/github/callback"
    assert _redirect_uri("/auth/google/callback") == "https://synapse.example.com/auth/google/callback"


def test_redirect_uri_falls_back_to_host_port_when_public_url_unset() -> None:
    from synapse.gateway.auth import _redirect_uri

    # The autouse fixture leaves SYNAPSE_PUBLIC_URL unset; the default is host:port.
    uri = _redirect_uri("/auth/github/callback")
    assert uri.startswith("http://127.0.0.1:8000")
    assert uri.endswith("/auth/github/callback")
