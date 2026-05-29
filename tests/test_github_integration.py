"""GitHub integration tests — mocked GitHub API, real DB + auth-store."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from synapse.gateway.auth import (
    AuthError,
    complete_github_authorization,
    start_github_authorization,
)
from synapse.graph import operations
from synapse.graph.models import NodeType
from synapse.integrations.github import (
    GithubError,
    get_authenticated_user,
    list_open_issues_assigned_to_me,
    sync_issues_to_questions,
)


@pytest.fixture(autouse=True)
def _set_github_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_CLIENT_ID", "Iv1.test-github-client-id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "test-github-client-secret")
    from synapse.config import reset_settings_cache
    reset_settings_cache()


@pytest.fixture(autouse=True)
def _stub_embeddings():  # type: ignore[no-untyped-def]
    """Skip Chroma writes during these tests (other layers already test embeddings)."""
    with patch.object(operations, "_embed_and_upsert", lambda node: None):
        yield


# ── Helpers ──────────────────────────────────────────────────────────────────


def _mock_response(payload: Any, status: int = 200) -> MagicMock:
    """Build a MagicMock httpx.Response with the given JSON payload + status."""
    r = MagicMock(spec=httpx.Response)
    r.status_code = status
    r.ok = 200 <= status < 300
    r.json = lambda: payload
    r.text = str(payload)
    return r


class _RoutingClient(MagicMock):
    """A MagicMock client that routes GET/POST to per-URL responses."""

    def __init__(self, *, post_response: MagicMock | None = None,
                 get_responses: dict[str, MagicMock] | None = None) -> None:
        super().__init__(spec=httpx.Client)
        self._post_response = post_response or _mock_response({"access_token": "default"})
        self._get_responses = get_responses or {}
        self.post = MagicMock(return_value=self._post_response)
        self.get = MagicMock(side_effect=self._get)

    def _get(self, url: str, *args: Any, **kwargs: Any) -> MagicMock:
        # Match by suffix so the GITHUB_API_BASE_URL prefix doesn't trip the dict lookup.
        for suffix, resp in self._get_responses.items():
            if url.endswith(suffix):
                return resp
        raise AssertionError(f"unmocked GET {url}")


def _seed_github_token() -> _RoutingClient:
    """Run the OAuth flow with a mock client so a github credential exists."""
    auth = start_github_authorization()
    token_exchange_client = _RoutingClient(
        post_response=_mock_response(
            {"access_token": "gho_test_token", "scope": "repo,read:user"}
        )
    )
    complete_github_authorization(
        code="fake-code", state=auth.state, http_client=token_exchange_client
    )
    return _RoutingClient()  # blank client to be filled per test


# ── get_authenticated_user ───────────────────────────────────────────────────


def test_get_authenticated_user_returns_profile() -> None:
    _seed_github_token()
    client = _RoutingClient(
        get_responses={
            "/user": _mock_response({"login": "wint3rx", "name": "Brian", "id": 12345}),
        },
    )
    user = get_authenticated_user(http_client=client)
    assert user["login"] == "wint3rx"
    assert user["id"] == 12345


def test_get_authenticated_user_raises_auth_error_on_401() -> None:
    _seed_github_token()
    client = _RoutingClient(
        get_responses={"/user": _mock_response({"message": "Bad credentials"}, status=401)},
    )
    with pytest.raises(AuthError, match="rejected"):
        get_authenticated_user(http_client=client)


# ── list_open_issues_assigned_to_me ──────────────────────────────────────────


def test_list_open_issues_normalises_payload() -> None:
    _seed_github_token()
    items = [
        {
            "id": 1, "number": 42, "title": "Refactor auth tests",
            "html_url": "https://github.com/xtensionlabs/synapse/issues/42",
            "repository_url": "https://api.github.com/repos/xtensionlabs/synapse",
            "state": "open",
            "body": "## Context\nWe should...",
        },
        {
            "id": 2, "number": 43, "title": "PR: Add GitHub OAuth",
            "html_url": "https://github.com/xtensionlabs/synapse/pull/43",
            "repository_url": "https://api.github.com/repos/xtensionlabs/synapse",
            "state": "open",
            "pull_request": {"url": "..."},  # presence flags this as a PR
        },
    ]
    client = _RoutingClient(get_responses={"/issues?filter=assigned&state=open&per_page=50": _mock_response(items)})
    # The query string differs based on params; match more loosely.
    client._get_responses = {"/issues": _mock_response(items)}

    out = list_open_issues_assigned_to_me(http_client=client)
    assert len(out) == 2
    assert out[0].title == "Refactor auth tests"
    assert out[0].repo_full_name == "xtensionlabs/synapse"
    assert out[0].is_pull_request is False
    assert out[1].is_pull_request is True


def test_list_open_issues_raises_github_error_on_500() -> None:
    _seed_github_token()
    client = _RoutingClient(
        get_responses={"/issues": _mock_response({"message": "server down"}, status=500)},
    )
    with pytest.raises(GithubError):
        list_open_issues_assigned_to_me(http_client=client)


# ── sync_issues_to_questions ─────────────────────────────────────────────────


def test_sync_creates_question_nodes_and_skips_prs() -> None:
    _seed_github_token()
    items = [
        {
            "id": 1, "number": 10, "title": "Investigate slow query",
            "html_url": "https://github.com/x/y/issues/10",
            "repository_url": "https://api.github.com/repos/x/y",
            "state": "open", "body": "p99 spiked yesterday",
        },
        {
            "id": 2, "number": 11, "title": "PR: bump deps",
            "html_url": "https://github.com/x/y/pull/11",
            "repository_url": "https://api.github.com/repos/x/y",
            "state": "open", "pull_request": {"url": "..."},
        },
    ]
    client = _RoutingClient(get_responses={"/issues": _mock_response(items)})

    created = sync_issues_to_questions(http_client=client)
    assert created == 1, "PR should be skipped, issue should create one node"

    node = operations.find_node_by_title("Investigate slow query")
    assert node is not None
    assert node.type == NodeType.QUESTION
    assert "x/y" in node.content
    assert "#10" in node.content


def test_sync_dedupes_by_title_on_second_run() -> None:
    _seed_github_token()
    items = [
        {
            "id": 1, "number": 10, "title": "Same issue twice",
            "html_url": "https://github.com/x/y/issues/10",
            "repository_url": "https://api.github.com/repos/x/y",
            "state": "open", "body": "",
        },
    ]
    client = _RoutingClient(get_responses={"/issues": _mock_response(items)})

    first = sync_issues_to_questions(http_client=client)
    second = sync_issues_to_questions(http_client=client)
    assert first == 1
    assert second == 0, "second sync must dedupe by title"


def test_sync_with_no_open_issues_creates_zero_nodes() -> None:
    _seed_github_token()
    client = _RoutingClient(get_responses={"/issues": _mock_response([])})
    assert sync_issues_to_questions(http_client=client) == 0
