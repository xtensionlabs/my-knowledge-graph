"""GitHub integration — read assigned issues + sync them into QUESTION nodes.

Like `google_calendar.py`, this module never sees a raw token directly. It
calls `synapse.gateway.auth.get_access_token("github")` which returns a
short-lived bearer string; we use it once and discard it.

Right now Synapse uses GitHub OAuth for ONE thing: pulling open issues
assigned to the authenticated user across all their repos, and creating
QUESTION nodes for them. The Strategist then sees those questions in its
weekly run and can surface deadlines / collisions.

What we do NOT do (yet):
    - Pull commits — that's already covered by the M3 post-commit hook
    - Pull PRs — same reasoning; commit + manifest gives us the BUILD picture
    - Mutate issues (post comments, close, etc.) — read-only by design
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger

from synapse.config import (
    ANTHROPIC_CONNECT_TIMEOUT_SECONDS,
    GITHUB_API_BASE_URL,
)
from synapse.gateway.auth import AuthError, get_access_token


@dataclass
class GithubIssue:
    """Normalised representation of a GitHub issue (or PR, since they share a type)."""

    id: int
    number: int
    title: str
    html_url: str
    repo_full_name: str         # e.g., "xtensionlabs/my-knowledge-graph"
    state: str                  # "open" | "closed"
    is_pull_request: bool
    body: str = ""


class GithubError(Exception):
    """Raised on GitHub API failures."""


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "synapse-cognitive-os",
    }


def get_authenticated_user(
    *, http_client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Return the authenticated user's profile (login + avatar_url + name + …).

    Useful as a connection test and to verify the stored token still has access.
    """
    token = get_access_token("github", http_client=http_client)
    client = http_client or httpx.Client(timeout=ANTHROPIC_CONNECT_TIMEOUT_SECONDS)
    try:
        resp = client.get(f"{GITHUB_API_BASE_URL}/user", headers=_headers(token))
        if resp.status_code == 401:
            raise AuthError("github token rejected — re-run `synapse auth github start`")
        if resp.status_code >= 400:
            raise GithubError(f"GET /user failed: {resp.status_code} {resp.text[:200]}")
        return resp.json()
    finally:
        if http_client is None:
            client.close()


def list_open_issues_assigned_to_me(
    *,
    limit: int = 50,
    http_client: httpx.Client | None = None,
) -> list[GithubIssue]:
    """Fetch open issues + PRs assigned to the authenticated user.

    GitHub's `/issues` endpoint (without owner/repo) returns issues across all
    repos the user has access to. The `filter=assigned` (default) restricts
    to ones the user is assigned. Pull requests come through this endpoint
    too — distinguished by the `pull_request` field on each item.
    """
    token = get_access_token("github", http_client=http_client)
    client = http_client or httpx.Client(timeout=ANTHROPIC_CONNECT_TIMEOUT_SECONDS)
    try:
        params = {"filter": "assigned", "state": "open", "per_page": str(limit)}
        resp = client.get(
            f"{GITHUB_API_BASE_URL}/issues",
            headers=_headers(token),
            params=params,
        )
        if resp.status_code == 401:
            raise AuthError("github token rejected — re-run `synapse auth github start`")
        if resp.status_code >= 400:
            raise GithubError(f"GET /issues failed: {resp.status_code} {resp.text[:200]}")
        items = resp.json()
    finally:
        if http_client is None:
            client.close()

    out: list[GithubIssue] = []
    for item in items:
        repo_url = item.get("repository_url", "")
        # repository_url format: https://api.github.com/repos/<owner>/<repo>
        repo_full_name = repo_url.removeprefix(f"{GITHUB_API_BASE_URL}/repos/")
        out.append(
            GithubIssue(
                id=int(item.get("id", 0)),
                number=int(item.get("number", 0)),
                title=str(item.get("title", "")),
                html_url=str(item.get("html_url", "")),
                repo_full_name=repo_full_name,
                state=str(item.get("state", "open")),
                is_pull_request="pull_request" in item,
                body=str(item.get("body") or "")[:1000],  # cap to avoid huge node content
            )
        )
    logger.info("github: fetched {n} open assigned issues/PRs", n=len(out))
    return out


def sync_issues_to_questions(
    *,
    limit: int = 50,
    http_client: httpx.Client | None = None,
) -> int:
    """Import open GitHub issues as QUESTION nodes (dedupe by title).

    PRs are skipped — those are captured by the M3 git-hook flow as BUILD
    updates. Only issues become QUESTIONs.

    Returns:
        Number of new QUESTION nodes created.
    """
    from synapse.graph.models import NodeType
    from synapse.graph.operations import create_node, find_node_by_title

    items = list_open_issues_assigned_to_me(limit=limit, http_client=http_client)
    created = 0
    for issue in items:
        if issue.is_pull_request:
            continue
        existing = find_node_by_title(issue.title)
        if existing is not None:
            continue  # dedupe by title
        content_lines = [
            f"**Source:** [{issue.repo_full_name}#{issue.number}]({issue.html_url})",
            "",
            issue.body or "_(no body)_",
        ]
        create_node(
            type=NodeType.QUESTION,
            title=issue.title,
            content="\n".join(content_lines),
            source_ids=[f"github:{issue.repo_full_name}#{issue.number}"],
            tags=[f"github:{issue.repo_full_name}"],
        )
        created += 1
    logger.info("github: created {n} QUESTION nodes from open issues", n=created)
    return created
