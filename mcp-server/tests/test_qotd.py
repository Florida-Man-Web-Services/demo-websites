"""Tests for AI 411 Question of the Day + people profile → events."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

MCP_DIR = Path(__file__).resolve().parent.parent
REPO = MCP_DIR.parent
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))
if str(REPO / "voice-agent") not in sys.path:
    sys.path.insert(0, str(REPO / "voice-agent"))


@pytest.fixture
def tmp_qotd(tmp_path, monkeypatch):
    qpath = tmp_path / "qotd.json"
    cpath = tmp_path / "callers.json"
    epath = tmp_path / "events.json"
    monkeypatch.setenv("QOTD_PATH", str(qpath))
    monkeypatch.setenv("CALLERS_PATH", str(cpath))
    monkeypatch.setenv("EVENTS_PATH", str(epath))
    # Reload modules so paths re-resolve
    for key in list(sys.modules):
        if key in ("qotd", "callers", "events"):
            del sys.modules[key]
    import qotd
    import callers
    import events

    importlib.reload(qotd)
    importlib.reload(callers)
    importlib.reload(events)
    return qotd, callers, events, qpath, cpath, epath


def test_get_qotd_is_people_oriented(tmp_qotd):
    qotd, *_ = tmp_qotd
    r = qotd.get_question_of_the_day(day="2026-08-14")
    assert r["ok"] is True
    assert r["text"]
    assert "?" in r["text"] or "you" in r["text"].lower()
    assert r.get("category") == "people"
    # Stable for same day
    r2 = qotd.get_question_of_the_day(day="2026-08-14")
    assert r2["question_id"] == r["question_id"]
    assert r2["text"] == r["text"]


def test_answer_builds_profile_and_tags(tmp_qotd):
    qotd, callers, *_ = tmp_qotd
    q = qotd.get_question_of_the_day(day="2026-08-14")
    out = qotd.answer_question_of_the_day(
        "+13555550100",
        "I love small live music nights with close friends and free outdoor shows",
        question_id=q["question_id"],
        day="2026-08-14",
    )
    assert out["ok"] and out["recorded"]
    assert "music" in out["tags"] or "outdoors" in out["tags"] or "free" in out["tags"]
    people = qotd.get_caller_people_profile("+13555550100")
    assert people["found"] is True
    assert people["answer_count"] == 1
    prof = callers.get_profile("+13555550100")
    assert prof.get("memory_ok") is True
    interests = (prof.get("preferences") or {}).get("interests") or []
    assert interests


def test_suggest_question_promotes_peopleish(tmp_qotd):
    qotd, *_ = tmp_qotd
    r = qotd.suggest_question_of_the_day(
        "+13555550100",
        "Who is the stranger you'd most want to meet at a community art night?",
    )
    assert r["ok"] and r["accepted"]
    assert r.get("promoted_to_bank") is True


def test_match_events_for_profile(tmp_qotd):
    qotd, callers, events, *_ = tmp_qotd
    # Seed events store
    events.ensure_seeded() if hasattr(events, "ensure_seeded") else None
    # Force seed via search
    events.search_events(limit=5)
    qotd.answer_question_of_the_day(
        "+13555550199",
        "Farmers markets and outdoor hangouts with friendly people",
        day="2026-08-15",
    )
    m = qotd.match_events_for_profile("+13555550199", when="", limit=5)
    assert m["ok"] is True
    assert "events" in m
    # may be empty if seeds don't match tags; still ok structure
    assert isinstance(m["events"], list)
