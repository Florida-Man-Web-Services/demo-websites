"""Validation and rendering for public client account pages.

This module is intentionally independent from ``personal_pages.py`` and the
outreach/generated-sites system.  Page content is bounded plain text only;
HTML is produced here and every user-controlled value is escaped at render
time. Rejection messages never include the rejected value because callers
may surface them to a model or record them in an audit trail.
"""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from typing import Any, Iterable

MAX_TITLE_LENGTH = 120
MAX_BODY_LENGTH = 8_000
MAX_LINE_LENGTH = 1_000

class PageValidationError(ValueError):
    """Raised when a page payload is not publishable plain text."""


_EMAIL_RE = re.compile(r"(?<![\w.+-])[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+")
_URL_RE = re.compile(
    r"(?:"
    r"\b(?:https?|ftp)://[^\s<>()]+"
    r"|\bwww\.[^\s<>()]+"
    r"|\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}"
    r"(?::\d{2,5})?(?:[/?#][^\s<>()]*)?"
    r")",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d().\- x]{5,}\d|\d{7,})(?!\w)")
_HTML_TAG_RE = re.compile(r"<\s*/?\s*[a-z][^>]*>", re.IGNORECASE)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _canonical_text(value: str, *, field: str, maximum: int, required: bool) -> str:
    if not isinstance(value, str):
        raise PageValidationError(f"{field} must be plain text")
    text = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    text = text.strip()
    if required and not text:
        raise PageValidationError(f"{field} is required")
    if len(text) > maximum:
        raise PageValidationError(f"{field} is too long")
    if any(len(line) > MAX_LINE_LENGTH for line in text.splitlines()):
        raise PageValidationError(f"{field} contains an overlong line")
    if _CONTROL_RE.search(text) or _HTML_TAG_RE.search(text) or "<" in text or ">" in text:
        raise PageValidationError(f"{field} must not contain markup or control characters")
    return text


def canonical_page_payload(title: str, body: str) -> tuple[str, str]:
    """Return the stable, normalized title/body pair used for hashing.

    Validation is done here as well as by :func:`validate_public_page` so a
    digest cannot be computed for a payload that the renderer would reject.
    """

    return (
        _canonical_text(title, field="title", maximum=MAX_TITLE_LENGTH, required=True),
        _canonical_text(body, field="body", maximum=MAX_BODY_LENGTH, required=True),
    )


def _contains_forbidden_content(value: str, known_identifiers: Iterable[str]) -> bool:
    lowered = value.casefold()
    if _EMAIL_RE.search(value) or _URL_RE.search(value) or _PHONE_RE.search(value):
        return True
    if isinstance(known_identifiers, str):
        known_identifiers = (known_identifiers,)
    return any(
        isinstance(identifier, str)
        and identifier.strip()
        and identifier.casefold() in lowered
        for identifier in known_identifiers
    )


def validate_public_page(
    title: str,
    body: str,
    *,
    known_identifiers: Iterable[str] = (),
) -> dict[str, Any]:
    """Validate bounded public text and return a safe canonical payload.

    ``known_identifiers`` is supplied by a server-owned context, for example
    an opaque account id or a phone/email lookup value.  It is never echoed in
    the returned error.
    """

    try:
        canonical_title = _canonical_text(
            title, field="title", maximum=MAX_TITLE_LENGTH, required=True
        )
        canonical_body = _canonical_text(
            body, field="body", maximum=MAX_BODY_LENGTH, required=True
        )
        if _contains_forbidden_content(canonical_title, known_identifiers):
            raise PageValidationError("title contains non-public contact or account data")
        if _contains_forbidden_content(canonical_body, known_identifiers):
            raise PageValidationError("body contains non-public contact or account data")
    except PageValidationError as exc:
        return {"ok": False, "error": str(exc)}

    digest = page_digest(canonical_title, canonical_body)
    return {"ok": True, "title": canonical_title, "body": canonical_body, "digest": digest}


def page_digest(title: str, body: str) -> str:
    """Hash the canonical page payload with an unambiguous length prefix."""

    canonical_title, canonical_body = canonical_page_payload(title, body)
    encoded = (
        len(canonical_title).to_bytes(4, "big")
        + canonical_title.encode("utf-8")
        + len(canonical_body).to_bytes(4, "big")
        + canonical_body.encode("utf-8")
    )
    return hashlib.sha256(encoded).hexdigest()


def render_client_page(page: dict[str, Any]) -> str:
    """Render a validated page as deterministic, self-contained HTML."""

    if not isinstance(page, dict):
        raise PageValidationError("page must be an object")
    result = validate_public_page(
        page.get("title"),  # type: ignore[arg-type]
        page.get("body"),  # type: ignore[arg-type]
        known_identifiers=page.get("known_identifiers", ()),
    )
    if not result["ok"]:
        raise PageValidationError(result["error"])

    title = html.escape(result["title"], quote=True)
    body = html.escape(result["body"], quote=True).replace("\n", "<br />\n")
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '  <meta charset="utf-8" />\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1" />\n'
        '  <meta name="robots" content="noindex,nofollow" />\n'
        f"  <title>{title}</title>\n"
        "</head>\n"
        "<body>\n"
        '  <main class="client-page">\n'
        f"    <h1>{title}</h1>\n"
        f'    <div class="page-body">{body}</div>\n'
        "  </main>\n"
        "</body>\n"
        "</html>\n"
    )
