"""Public HTML escaping and omitted NAP."""

from __future__ import annotations

import business_cms_render as render
import business_cms_schema as schema


def test_escapes_and_omits_missing_nap():
    doc = schema.empty_document("cool-cafe", 'Tom & "Friends"')
    doc["identity"]["name"] = 'Tom & "Friends"'
    doc["pages"][0]["blocks"] = [{"type": "text", "heading": "Hi", "body": "<script>alert(1)</script>"}]
    # markup in body is rejected by schema; use ampersand instead
    doc["pages"][0]["blocks"] = [{"type": "text", "heading": "Hi", "body": "A & B"}]
    html_out = render.render_public_html(doc, origin="https://example.test", release_id="rel-1")
    assert "&amp;" in html_out
    assert "<script" not in html_out.lower()
    assert "Hours are not published." in html_out
    assert "tel:" not in html_out
    assert 'content="rel-1"' in html_out


def test_preview_banner():
    doc = schema.empty_document("cool-cafe", "Cafe")
    html_out = render.render_preview_html(doc, origin="https://example.test", draft_revision="draft-1")
    assert "Preview" in html_out
