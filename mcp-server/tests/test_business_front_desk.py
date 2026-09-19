"""Front-desk published knowledge and inbox persistence."""

from __future__ import annotations

import importlib
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import business_cms_schema as schema
import business_cms_store as store
import business_front_desk as desk
import business_inbox as inbox


@pytest.fixture
def published(tmp_path, monkeypatch):
    monkeypatch.setenv("BUSINESS_CMS_ENABLED", "true")
    monkeypatch.setenv("FRONT_DESK_ENABLED", "true")
    monkeypatch.setenv("BUSINESS_CMS_DATA_DIR", str(tmp_path / "cms"))
    importlib.reload(store)
    importlib.reload(inbox)
    importlib.reload(desk)
    doc = schema.empty_document("cool-cafe", "Cool Cafe")
    doc["hours"] = {
        "timezone": "America/New_York",
        "unknown": False,
        "weekly": {
            "mon": [{"open": "09:00", "close": "17:00"}],
            "tue": [{"open": "09:00", "close": "17:00"}],
            "wed": [{"open": "09:00", "close": "17:00"}],
            "thu": [{"open": "09:00", "close": "17:00"}],
            "fri": [{"open": "09:00", "close": "17:00"}],
            "sat": [],
            "sun": [],
        },
        "exceptions": [{"date": "2026-12-25", "closed": True}],
    }
    doc["faq"] = [{"id": "q1", "question": "Do you take walk-ins?", "answer": "Yes.", "visible": True, "order": 0}]
    doc["identity"]["name"] = "Cool Cafe"
    saved = store.save_draft("cool-cafe", doc, expected_draft_rev=None, actor="o")
    pub = store.publish(
        "cool-cafe",
        expected_draft_rev=saved["draft_revision"],
        expected_release_id=None,
        actor="o",
        html="<html><body>Cool Cafe</body></html>",
    )
    return pub


def test_get_business_excludes_draft_and_raw_phones(published, tmp_path):
    result = desk.get_business("cool-cafe", "identity")
    assert result["ok"]
    assert "provenance" not in (result["data"].get("identity") or result["data"])
    assert result["data"].get("identity", result["data"]).get("public_phone") is None


def test_hours_overnight_exception_and_unknown(published):
    tz = ZoneInfo("America/New_York")
    monday = datetime(2026, 9, 21, 12, 0, tzinfo=tz)
    assert desk.hours_status(desk.get_business("cool-cafe")["data"], at=monday)["open"] is True
    holiday = datetime(2026, 12, 25, 12, 0, tzinfo=tz)
    assert desk.hours_status(desk.get_business("cool-cafe")["data"], at=holiday)["open"] is False
    unknown = {"hours": {"unknown": True}}
    assert desk.hours_status(unknown)["known"] is False


def test_message_idempotent_and_receipt_has_no_contact(published):
    first = desk.leave_message(
        "cool-cafe",
        "Please call about catering.",
        use_caller_callback=True,
        contact_ref="opaque-ref-1",
        idempotency_key="same-key",
    )
    second = desk.leave_message(
        "cool-cafe",
        "Please call about catering.",
        use_caller_callback=True,
        contact_ref="opaque-ref-1",
        idempotency_key="same-key",
    )
    assert first["request_id"] == second["request_id"]
    assert "contact" not in first
    assert first.get("contact_ref") is None
    rows = inbox.list_requests("cool-cafe")
    assert len(rows) == 1
    assert rows[0]["has_contact_ref"] is True
    assert "opaque-ref-1" not in str(rows[0]["message"])


def test_appointment_is_not_a_booking(published):
    receipt = desk.request_appointment(
        "cool-cafe",
        service_id="cut",
        requested_time_text="tomorrow 3pm",
        notes="haircut",
        use_caller_callback=False,
        contact_ref=None,
        idempotency_key="appt-1",
    )
    assert receipt["booking"] is False
    assert receipt["status"] == "pending_owner_review"


def test_visitor_cannot_patch_without_owner_path(published):
    desk.leave_message("cool-cafe", "hi", use_caller_callback=False, contact_ref=None, idempotency_key="m1")
    rows = inbox.list_requests("cool-cafe")
    patched = inbox.patch_request("cool-cafe", rows[0]["id"], status="acknowledged")
    assert patched["request"]["status"] == "acknowledged"
    deleted = inbox.delete_request("cool-cafe", rows[0]["id"])
    assert deleted["ok"]
    assert inbox.list_requests("cool-cafe") == []


def test_disabled_front_desk(published, monkeypatch):
    monkeypatch.setenv("FRONT_DESK_ENABLED", "false")
    importlib.reload(desk)
    with pytest.raises(desk.FrontDeskError) as exc:
        desk.get_business("cool-cafe")
    assert exc.value.code == "feature_disabled"
