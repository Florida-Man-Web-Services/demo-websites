"""Kitchen-sink CMS schema validation."""

from __future__ import annotations

import pytest

import business_cms_schema as schema


def test_empty_document_preserves_missing_nap():
    doc = schema.empty_document("cool-cafe")
    assert doc["slug"] == "cool-cafe"
    assert doc["identity"]["name"] is None
    assert doc["identity"]["address"] is None
    assert doc["identity"]["public_phone"] is None
    assert doc["hours"]["unknown"] is True
    canonical = schema.validate_document(doc)
    assert canonical["identity"]["public_phone"] is None


def test_rejects_markup_and_invalid_slug():
    with pytest.raises(schema.CmsValidationError):
        schema.canonical_slug("../etc")
    with pytest.raises(schema.CmsValidationError):
        schema.canonical_slug("Cool Cafe")
    doc = schema.empty_document("cool-cafe", "Cafe")
    doc["identity"]["name"] = "<script>x</script>"
    with pytest.raises(schema.CmsValidationError):
        schema.validate_document(doc)


def test_publish_rejects_visible_later_wave_collections():
    doc = schema.empty_document("cool-cafe", "Cafe")
    doc["staff"] = [{"id": "s1", "name": "Pat", "visible": True}]
    schema.validate_document(doc, for_publish=False)
    with pytest.raises(schema.CmsValidationError):
        schema.validate_document(doc, for_publish=True)


def test_publish_rejects_extra_public_pages():
    doc = schema.empty_document("cool-cafe", "Cafe")
    doc["pages"].append({"id": "about", "slug": "about", "title": "About", "visible": True, "blocks": []})
    with pytest.raises(schema.CmsValidationError):
        schema.validate_document(doc, for_publish=True)


def test_public_projection_strips_provenance_and_hidden_faq():
    doc = schema.empty_document("cool-cafe", "Cafe")
    doc["identity"]["provenance"] = {"name": "owner"}
    doc["faq"] = [
        {"id": "q1", "question": "Hours?", "answer": "Nine to five.", "visible": True, "order": 1},
        {"id": "q2", "question": "Secret?", "answer": "No.", "visible": False, "order": 0},
    ]
    pub = schema.public_projection(doc)
    assert "provenance" not in pub["identity"]
    assert [item["id"] for item in pub["faq"]] == ["q1"]


def test_duplicate_ids_rejected():
    doc = schema.empty_document("cool-cafe")
    doc["services"] = {
        "groups": [
            {
                "id": "g1",
                "name": "Cuts",
                "items": [
                    {"id": "i1", "name": "Trim"},
                    {"id": "i1", "name": "Other"},
                ],
            }
        ]
    }
    with pytest.raises(schema.CmsValidationError):
        schema.validate_document(doc)


def test_hours_intervals_and_exceptions():
    doc = schema.empty_document("cool-cafe")
    doc["hours"] = {
        "timezone": "America/New_York",
        "unknown": False,
        "weekly": {"mon": [{"open": "09:00", "close": "17:00"}], "tue": [], "wed": [], "thu": [], "fri": [], "sat": [], "sun": []},
        "exceptions": [{"date": "2026-12-25", "closed": True, "note": "Holiday"}],
    }
    out = schema.validate_document(doc)
    assert out["hours"]["weekly"]["mon"][0]["open"] == "09:00"
    assert out["hours"]["exceptions"][0]["closed"] is True


def test_unsafe_url_rejected():
    doc = schema.empty_document("cool-cafe")
    doc["identity"]["website"] = "javascript:alert(1)"
    with pytest.raises(schema.CmsValidationError):
        schema.validate_document(doc)
