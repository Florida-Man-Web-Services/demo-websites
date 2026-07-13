"""Unit tests for caller profile store (#49 MVP)."""

import json

import pytest

import callers


@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    path = tmp_path / "callers.json"
    monkeypatch.setattr(callers, "CALLERS_PATH", path)
    return path


def test_normalize_phone_us():
    assert callers._normalize_phone("352-555-0100") == "+13525550100"
    assert callers._normalize_phone("+1 (352) 555-0100") == "+13525550100"
    assert callers._normalize_phone("13525550100") == "+13525550100"
    assert callers._normalize_phone("") is None
    assert callers._normalize_phone("123") is None


def test_get_missing(tmp_store):
    result = callers.get_profile("+13525550100")
    assert result == {"found": False, "phone_e164": "+13525550100"}


def test_get_invalid_phone(tmp_store):
    result = callers.get_profile("not-a-phone")
    assert result["found"] is False
    assert "error" in result


def test_update_creates_and_merges(tmp_store):
    created = callers.update_profile(
        "3525550100",
        {
            "display_name": "Noah Jones",
            "preferred_name": "Noah",
            "preferences": {
                "interests": ["live music"],
                "preferred_areas": ["downtown"],
            },
            "consent": {"memory_ok": True},
        },
    )
    assert created["updated"] is True
    assert created["phone_e164"] == "+13525550100"
    prof = created["profile"]
    assert prof["found"] is True
    assert prof["display_name"] == "Noah Jones"
    assert prof["preferred_name"] == "Noah"
    assert prof["preferences"]["interests"] == ["live music"]
    assert prof["preferences"]["sms_ok"] is False  # default retained
    assert prof["consent"]["memory_ok"] is True
    assert prof["consent"]["marketing_ok"] is False
    assert "created_at" in prof and "updated_at" in prof

    # Patch merges preferences without wiping other keys.
    updated = callers.update_profile(
        "+13525550100",
        {
            "preferences": {"avoid": ["cover charges"], "interests": ["jazz"]},
            "last_topics": ["weekend events"],
        },
    )
    assert updated["updated"] is True
    p2 = updated["profile"]
    assert p2["display_name"] == "Noah Jones"
    assert p2["preferences"]["interests"] == ["jazz"]
    assert p2["preferences"]["avoid"] == ["cover charges"]
    assert p2["preferences"]["preferred_areas"] == ["downtown"]
    assert p2["last_topics"] == ["weekend events"]

    # File shape: wrapped profiles map
    raw = json.loads(tmp_store.read_text(encoding="utf-8"))
    assert "+13525550100" in raw["profiles"]


def test_get_respects_memory_ok_false(tmp_store):
    callers.update_profile(
        "+13525550999",
        {
            "display_name": "Secret",
            "preferred_name": "Sec",
            "preferences": {"interests": ["private hobby"]},
            "last_topics": ["something personal"],
            "consent": {"memory_ok": False},
        },
    )
    # add note while memory off
    callers.add_note("+13525550999", "should not surface on get")

    got = callers.get_profile("+13525550999")
    assert got["found"] is True
    assert got["memory_ok"] is False
    assert got["display_name"] == ""
    assert got["preferred_name"] == ""
    assert got["notes"] == []
    assert got["last_topics"] == []
    assert got["preferences"]["interests"] == []
    assert "message" in got
    assert got["consent"]["memory_ok"] is False


def test_get_full_when_memory_ok(tmp_store):
    callers.update_profile(
        "+13525550111",
        {
            "display_name": "Alex",
            "consent": {"memory_ok": True},
            "preferences": {"interests": ["farmers markets"]},
        },
    )
    callers.add_note("+13525550111", "likes free outdoor stuff")
    got = callers.get_profile("352-555-0111")
    assert got["found"] is True
    assert got["memory_ok"] is True
    assert got["display_name"] == "Alex"
    assert got["preferences"]["interests"] == ["farmers markets"]
    assert len(got["notes"]) == 1
    assert got["notes"][0]["text"] == "likes free outdoor stuff"


def test_forget_hard_deletes(tmp_store):
    callers.update_profile(
        "+13525550222",
        {"display_name": "Gone", "consent": {"memory_ok": True}},
    )
    assert callers.get_profile("+13525550222")["found"] is True

    result = callers.forget_profile("+13525550222")
    assert result["forgotten"] is True
    assert result["existed"] is True

    assert callers.get_profile("+13525550222")["found"] is False
    raw = json.loads(tmp_store.read_text(encoding="utf-8"))
    assert "+13525550222" not in raw["profiles"]

    # Idempotent forget
    again = callers.forget_profile("+13525550222")
    assert again["forgotten"] is True
    assert again["existed"] is False


def test_add_note_creates_profile(tmp_store):
    result = callers.add_note("3525550333", "  first note  ")
    assert result["added"] is True
    assert result["phone_e164"] == "+13525550333"
    assert result["note"]["text"] == "first note"
    assert result["note_count"] == 1

    # memory_ok default false → get redacts notes
    got = callers.get_profile("+13525550333")
    assert got["found"] is True
    assert got["memory_ok"] is False
    assert got["notes"] == []

    # Enable memory and notes appear
    callers.update_profile("+13525550333", {"consent": {"memory_ok": True}})
    got2 = callers.get_profile("+13525550333")
    assert len(got2["notes"]) == 1


def test_add_note_empty_rejected(tmp_store):
    result = callers.add_note("+13525550100", "   ")
    assert result["added"] is False
    assert "error" in result


def test_update_rejects_bad_phone(tmp_store):
    result = callers.update_profile("x", {"display_name": "Nope"})
    assert result["updated"] is False
    assert "error" in result
