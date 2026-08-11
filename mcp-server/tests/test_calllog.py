import csv

import pytest

import businesses
import calldb
import calllog
import config


@pytest.fixture
def tmp_log(tmp_path, monkeypatch):
    log = tmp_path / "call-log.csv"
    db = tmp_path / "call-log.db"
    monkeypatch.setattr(config, "CALL_LOG", log)
    monkeypatch.setattr(config, "CALL_DB", db)
    monkeypatch.setattr(config, "CALL_LOG_DUAL_WRITE_CSV", True)
    calldb._initialized_paths.clear()
    return log


def biz():
    return businesses.Business(name="Ole Barn", phone="352-555-0199")


def test_append_creates_header_and_row(tmp_log):
    result = calllog.append_outcome(biz(), "interested", "Loved the demo.")
    assert result["logged"] is True
    assert result["call_sid"].startswith("XAI-")
    rows = list(csv.DictReader(open(tmp_log, encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["business"] == "Ole Barn"
    assert rows[0]["slug"] == "ole-barn"
    assert rows[0]["outcome"] == "interested"
    assert rows[0]["call_sid"].startswith("XAI-")
    assert rows[0]["direction"] == ""
    assert rows[0]["phone"] == biz().phone
    assert list(rows[0].keys()) == [
        "timestamp", "call_sid", "direction", "business", "slug",
        "phone", "outcome", "email", "callback_time", "notes",
    ]
    # Relational store also has the row (transcript_id NULL until turns flush).
    row = calldb.get_call(result["call_sid"])
    assert row is not None
    assert row["outcome"] == "interested"
    assert row["transcript_id"] is None


def test_append_caller_phone_used_for_do_not_call(tmp_log):
    result = calllog.append_outcome(
        biz(), "do_not_call", "opt out", caller_phone="+135****0000"
    )
    assert result["logged"] is True
    rows = list(csv.DictReader(open(tmp_log, encoding="utf-8")))
    assert rows[0]["phone"] == "+135****0000"


def test_append_rejects_bad_outcome(tmp_log):
    result = calllog.append_outcome(biz(), "hung_up_angry", "notes")
    assert result["logged"] is False
    assert result["valid_outcomes"] == calllog.VALID_OUTCOMES
    assert not tmp_log.exists()


def test_history_matches_slug_only(tmp_log):
    calllog.append_outcome(biz(), "interested", "first call")
    calllog.append_outcome(
        businesses.Business(name="Salty Dog Saloon"), "voicemail", "left vm"
    )
    rows = calllog.history_for("ole-barn")
    assert len(rows) == 1
    assert rows[0]["notes"] == "first call"
    assert "transcript_ref" in rows[0]
    assert calllog.history_for("nobody-here") == []


def test_history_no_file(tmp_log):
    assert calllog.history_for("ole-barn") == []
