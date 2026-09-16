"""Focused tests for the isolated client-account page boundary."""

from __future__ import annotations

import html

import pytest

import account_pages as pages


def test_validation_accepts_bounded_plain_text_and_digest_is_stable():
    result = pages.validate_public_page("  A helpful title  ", "line one\r\nline two")

    assert result["ok"] is True
    assert result["title"] == "A helpful title"
    assert result["body"] == "line one\nline two"
    assert result["digest"] == pages.page_digest("A helpful title", "line one\nline two")
    assert pages.page_digest("A helpful title", "changed") != result["digest"]


def test_validation_rejects_markup_links_contact_and_phone_content():
    bad_values = (
        "<script>alert(1)</script>",
        "https://example.test/about",
        "owner@example.test",
        "Call 352-555-0100",
    )

    for value in bad_values:
        result = pages.validate_public_page("Title", value)
        assert result["ok"] is False
        assert "error" in result
        assert value not in result["error"]


def test_validation_rejects_known_account_identifier_without_echoing_it():
    result = pages.validate_public_page(
        "Title",
        "A normal description for acct-secret-42",
        known_identifiers={"acct-secret-42"},
    )

    assert result["ok"] is False
    assert "acct-secret-42" not in result["error"]


def test_validation_enforces_nonempty_and_bounds():
    assert pages.validate_public_page("", "body")["ok"] is False
    assert pages.validate_public_page("Title", "")["ok"] is False
    assert pages.validate_public_page("x" * (pages.MAX_TITLE_LENGTH + 1), "body")["ok"] is False
    assert pages.validate_public_page("Title", "x" * (pages.MAX_BODY_LENGTH + 1))["ok"] is False


def test_renderer_escapes_text_and_is_deterministic():
    page = {"title": 'Tom & "Friends"', "body": 'hello & "friends"'}

    rendered_once = pages.render_client_page(page)
    rendered_twice = pages.render_client_page(page)

    assert rendered_once == rendered_twice
    assert html.escape(page["title"]) in rendered_once
    assert html.escape(page["body"]) in rendered_once
    assert 'Tom & "Friends"' not in rendered_once
    assert "<script" not in rendered_once.lower()
    assert "href=" not in rendered_once.lower()


def test_renderer_rejects_invalid_payload_instead_of_rendering_markup():
    with pytest.raises(pages.PageValidationError):
        pages.render_client_page({"title": "Title", "body": "<b>unsafe</b>"})
