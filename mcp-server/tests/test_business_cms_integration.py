"""End-to-end CMS publish → public page → inbox."""

from __future__ import annotations

import json
from pathlib import Path

import business_cms_http as http
import business_cms_render as render
import business_cms_schema as schema


def test_fixture_render_matches_golden():
    root = Path(__file__).resolve().parent / "fixtures"
    raw = json.loads((root / "business_cms.json").read_text(encoding="utf-8"))
    html = render.render_public_html(
        schema.validate_document(raw, for_publish=True),
        origin="https://example.test",
        release_id="rel-fixture",
    )
    expected = (root / "business_cms_expected.html").read_text(encoding="utf-8")
    assert html == expected
    assert "<script" not in html.lower()
    assert "Hours" in html
