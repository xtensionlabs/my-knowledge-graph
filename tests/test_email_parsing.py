"""Tests for email body cleaning + URL extraction."""

from __future__ import annotations

from synapse.capture.email_ingest import parse_email


def test_quoted_reply_stripped() -> None:
    body = (
        "Reminder: CAT this Friday.\n"
        "\n"
        "On Mon, May 22, Prof Mwangi wrote:\n"
        "> The exam will cover chapters 1-4.\n"
        "> Please come prepared.\n"
    )
    parsed = parse_email(json_payload={"from": "p@x", "subject": "s", "body": body})
    assert "Reminder: CAT this Friday." in parsed.clean_body
    assert "Prof Mwangi wrote" not in parsed.clean_body
    assert "chapters 1-4" not in parsed.clean_body


def test_signature_stripped() -> None:
    body = "real content here\n\n-- \nProf Mwangi\nStrathmore CS\n"
    parsed = parse_email(json_payload={"from": "p@x", "subject": "s", "body": body})
    assert "real content here" in parsed.clean_body
    assert "Strathmore CS" not in parsed.clean_body


def test_urls_extracted_in_order() -> None:
    body = "see https://arxiv.org/abs/1234 and https://github.com/x/y for refs"
    parsed = parse_email(json_payload={"from": "p", "subject": "s", "body": body})
    assert parsed.urls == ["https://arxiv.org/abs/1234", "https://github.com/x/y"]


def test_json_envelope_round_trip() -> None:
    parsed = parse_email(
        json_payload={
            "from": "alice@example.com",
            "subject": "hi",
            "body": "single line body",
            "message_id": "<abc@example.com>",
        }
    )
    assert parsed.from_ == "alice@example.com"
    assert parsed.subject == "hi"
    assert parsed.clean_body == "single line body"
    assert parsed.message_id == "<abc@example.com>"


def test_rfc822_or_json_required() -> None:
    import pytest

    with pytest.raises(ValueError):
        parse_email()  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        parse_email(raw=b"x", json_payload={"x": 1})
