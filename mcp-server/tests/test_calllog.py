import csv

import pytest

import businesses
import calllog
import config


@pytest.fixture
def tmp_log(tmp_path, monkeypatch):
    log = tmp_path / "call-log.csv"
    monkeypatch.setattr(config, "CALL_LOG", log)
    return log


def biz():
    return businesses.Business(name="Ole Barn", phone="352-555-0199")


def test_append_creates_header_and_row(tmp_log):
    result = calllog.append_outcome(biz(), "interested", "Loved the demo.")
    assert result == {"logged": True}
    rows = list(csv.DictReader(open(tmp_log, encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["business"] == "Ole Barn"
    assert rows[0]["slug"] == "ole-barn"
    assert rows[0]["outcome"] == "interested"
    assert rows[0]["call_sid"].startswith("XAI-")
    assert list(rows[0].keys()) == [
        "timestamp", "call_sid", "direction", "business", "slug",
        "phone", "outcome", "email", "callback_time", "notes",
    ]


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
    assert calllog.history_for("nobody-here") == []


def test_history_no_file(tmp_log):
    assert calllog.history_for("ole-barn") == []
