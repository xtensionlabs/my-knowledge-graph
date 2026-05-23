"""Tests for clipboard daemon qualification rules + dedup."""

from __future__ import annotations

from collections import deque

from synapse.capture.clipboard import _hash, _looks_sensitive, _qualifies
from synapse.config import CLIPBOARD_MIN_LENGTH


def test_short_text_is_skipped() -> None:
    short = "x" * (CLIPBOARD_MIN_LENGTH - 1)
    assert not _qualifies(short, deque())


def test_long_text_qualifies() -> None:
    long = "a real thought that compounds " * 4
    assert _qualifies(long, deque())


def test_credential_shaped_content_is_skipped() -> None:
    samples = [
        "password = hunter2hunter2hunter2hunter2hunter2",
        "api_key = abcdef0123456789" * 3,
        "ghp_" + "a" * 36,
        "-----BEGIN RSA PRIVATE KEY-----",
        "sk-" + "x" * 40,
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ.signaturepartXYZ123abc",
    ]
    for s in samples:
        assert _looks_sensitive(s), f"failed to skip: {s!r}"


def test_dedup_window_prevents_repeats() -> None:
    text = "the same clipboard content repeated within the dedup window of fifty entries"
    recent: deque[str] = deque(maxlen=50)
    assert _qualifies(text, recent)
    recent.append(_hash(text))
    assert not _qualifies(text, recent)


def test_non_string_input_skipped() -> None:
    assert not _qualifies(None, deque())
    assert not _qualifies("", deque())


def test_whitespace_only_skipped() -> None:
    assert not _qualifies("\n   \t   \n", deque())
