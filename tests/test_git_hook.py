"""Tests for synapse/capture/git_hook.py — post-commit hook installer."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from synapse.capture.git_hook import install_hook
from synapse.config import GIT_HOOK_DEFAULT_GATEWAY, GIT_HOOK_SCRIPT_NAME


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repository in tmp_path."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    return tmp_path


# ── Installation tests ────────────────────────────────────────────────────────


def test_install_hook_creates_file(git_repo: Path) -> None:
    hook_path = install_hook(git_repo)
    assert hook_path.is_file()
    assert hook_path.name == GIT_HOOK_SCRIPT_NAME


def test_install_hook_returns_correct_path(git_repo: Path) -> None:
    hook_path = install_hook(git_repo)
    expected = git_repo / ".git" / "hooks" / GIT_HOOK_SCRIPT_NAME
    assert hook_path == expected


def test_install_hook_contains_gateway_url(git_repo: Path) -> None:
    hook_path = install_hook(git_repo, gateway_url="http://127.0.0.1:9999")
    content = hook_path.read_text(encoding="utf-8")
    assert "http://127.0.0.1:9999/ingest/git" in content


def test_install_hook_default_gateway_url(git_repo: Path) -> None:
    hook_path = install_hook(git_repo)
    content = hook_path.read_text(encoding="utf-8")
    assert GIT_HOOK_DEFAULT_GATEWAY in content


def test_install_hook_has_python_shebang(git_repo: Path) -> None:
    hook_path = install_hook(git_repo)
    first_line = hook_path.read_text(encoding="utf-8").splitlines()[0]
    assert first_line.startswith("#!/usr/bin/env python3")


def test_install_hook_overwrites_existing(git_repo: Path) -> None:
    """Calling install_hook twice must replace the old script."""
    install_hook(git_repo, gateway_url="http://old:8000")
    install_hook(git_repo, gateway_url="http://new:9999")
    content = (git_repo / ".git" / "hooks" / GIT_HOOK_SCRIPT_NAME).read_text()
    assert "http://new:9999" in content
    assert "http://old:8000" not in content


@pytest.mark.skipif(sys.platform == "win32", reason="chmod not meaningful on Windows")
def test_install_hook_is_executable(git_repo: Path) -> None:
    hook_path = install_hook(git_repo)
    assert hook_path.stat().st_mode & 0o111, "hook script is not executable"


def test_install_hook_rejects_non_git_directory(tmp_path: Path) -> None:
    """Raise ValueError when target has no .git/ subdirectory."""
    with pytest.raises(ValueError, match="Not a git repository"):
        install_hook(tmp_path)


def test_install_hook_creates_hooks_directory(tmp_path: Path) -> None:
    """hooks/ dir should be created if absent."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    # Deliberately do NOT create hooks/ — install_hook must create it.
    hook_path = install_hook(tmp_path)
    assert hook_path.is_file()


def test_hook_script_uses_stdlib_only(git_repo: Path) -> None:
    """Hook script must not import third-party packages (no venv at hook time)."""
    content = install_hook(git_repo).read_text(encoding="utf-8")
    # The script must only import from stdlib
    for forbidden in ("requests", "httpx", "aiohttp", "pydantic"):
        assert forbidden not in content, f"hook imports {forbidden!r} — stdlib only"
