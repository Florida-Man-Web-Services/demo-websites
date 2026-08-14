"""Tests for AI 411 FOMO tribe interest matching."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import callers
import events
import fomo


@pytest.fixture
def stores(tmp_path, monkeypatch):
    callers_path = tmp_path / "callers.json"
    interests_path = tmp_path / "event_interests.jsonl"
    notify_path = tmp_path / "fomo_notify.jsonl"
    events_path = tmp_path / "events.json"

    monkeypatch.setattr(callers, "CALLERS_PATH", callers_path)
    monkeypatch.setattr(fomo, "EVENT_INTERESTS_PATH", interests_path)
    monkeypatch.setattr(fomo, "FOMO_NOTIFY_PATH", notify_path)
    monkeypatch.setenv("EVENT_INTERESTS_PATH", str(interests_path))
    monkeypatch.setenv("FOMO_NOTIFY_PATH", str(notify_path))
    monkeypatch.setenv("EVENTS_PATH", str(events_path))
    monkeypatch.setattr(events, "EVENTS_PATH", events_path)

    # Minimal seed event
    events_path.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "id": "evt-seed-live-jazz",
                        "title": "Live Jazz at The Top",
                        "start": "2099-08-15T20:00:00-04:00",
                        "end": "2099-08-15T23:00:00-04:00",
                        "venue": "The Top",
                        "address": "30 N Main St, Gainesville, FL",
                        "free": False,
                        "tags": ["music", "jazz", "nightlife"],
                        "description": "Local jazz trio.",
                        "url": "",
                        "source": "seed",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    # Force events module to re-read path helpers
    return {
        "callers": callers_path,
        "interests": interests_path,
        "notify": notify_path,
        "events": events_path,
    }


def _enable(phone: str, *, fomo_ok: bool = False, sms_ok: bool = False):
    patch = {
        "consent": {"memory_ok": True, "fomo_ok": fomo_ok},
        "preferences": {
            "interests": ["music", "jazz"],
            "sms_ok": sms_ok,
        },
    }
    r = callers.update_profile(phone, patch)
    assert r["updated"] is True
    return r


def test_fomo_ok_default_off(stores):
    r = callers.update_profile(
        "+13525550100",
        {"consent": True, "preferences": {"interests": ["music"]}},
    )
    assert r["profile"]["consent"]["memory_ok"] is True
    assert r["profile"]["consent"].get("fomo_ok") is False
    assert r["profile"]["preferences"].get("fomo_calls") is False


def test_fomo_calls_alias_sets_consent(stores):
    r = callers.update_profile(
        "+13525550101",
        {
            "consent": {"memory_ok": True},
            "preferences": {"fomo_calls": True, "sms_ok": True},
        },
    )
    assert r["profile"]["consent"]["fomo_ok"] is True
    assert r["profile"]["preferences"]["fomo_calls"] is True


def test_express_requires_memory_ok(stores):
    out = fomo.express_event_interest("+13525550102", "evt-seed-live-jazz")
    assert out["ok"] is False
    assert out.get("needs_memory_ok") is True
    assert out.get("recorded") is False


def test_express_without_fomo_records_and_prompts(stores):
    _enable("+13525550103", fomo_ok=False)
    out = fomo.express_event_interest("+13525550103", "evt-seed-live-jazz")
    assert out["ok"] is True
    assert out["recorded"] is True
    assert out.get("needs_fomo_ok") is True
    assert out.get("notifies_queued", 0) == 0
    assert "FOMO" in (out.get("speakable") or "")
    # interest on disk
    lines = stores["interests"].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    row = json.loads(lines[-1])
    assert row["event_id"] == "evt-seed-live-jazz"
    assert row["phone_e164"] == "+13525550103"


def test_matcher_requires_two_fomo_ok_and_queues_sms(stores):
    a = "+13525550110"
    b = "+13525550111"
    _enable(a, fomo_ok=True, sms_ok=True)
    _enable(b, fomo_ok=True, sms_ok=True)

    r1 = fomo.express_event_interest(a, "evt-seed-live-jazz", tags=["music"])
    assert r1["recorded"] is True
    assert r1.get("notifies_queued", 0) == 0  # only one peer so far

    r2 = fomo.express_event_interest(b, "evt-seed-live-jazz", tags=["music"])
    assert r2["recorded"] is True
    assert r2["peer_matches"] >= 2
    assert r2["notifies_queued"] >= 1

    jobs = fomo.list_notify_queue(limit=20)
    assert jobs["ok"] is True
    assert jobs["count"] >= 1
    sms_jobs = [j for j in jobs["jobs"] if j.get("type") == "sms"]
    assert sms_jobs
    for j in sms_jobs:
        assert j.get("status") == "queued"
        assert "STOP" in (j.get("template") or "")
        # template must not leak a second phone as spoken peer identity beyond to_phone field
        assert "+135" not in (j.get("template") or "").replace(j.get("to_phone", ""), "")


def test_no_notify_when_second_caller_lacks_fomo(stores):
    a = "+13525550120"
    b = "+13525550121"
    _enable(a, fomo_ok=True, sms_ok=True)
    _enable(b, fomo_ok=False, sms_ok=True)

    fomo.express_event_interest(a, "evt-seed-live-jazz")
    r2 = fomo.express_event_interest(b, "evt-seed-live-jazz")
    assert r2["recorded"] is True
    assert r2.get("needs_fomo_ok") is True
    assert r2.get("notifies_queued", 0) == 0
    jobs = fomo.list_notify_queue(limit=50)
    assert jobs["count"] == 0


def test_list_matches_privacy_and_gates(stores):
    a = "+13525550130"
    b = "+13525550131"
    _enable(a, fomo_ok=True, sms_ok=True)
    _enable(b, fomo_ok=True, sms_ok=True)
    fomo.express_event_interest(a, "evt-seed-live-jazz")
    fomo.express_event_interest(b, "evt-seed-live-jazz")

    listed = fomo.list_event_interest_matches(a)
    assert listed["ok"] is True
    assert listed["count"] >= 1
    blob = json.dumps(listed)
    assert b not in blob  # peer phone never in payload
    assert "someone else" in listed["speakable"].lower() or "others" in listed["speakable"].lower()
    for m in listed["matches"]:
        assert "phone" not in m
        assert "phones" not in m

    # without fomo
    c = "+13525550132"
    _enable(c, fomo_ok=False)
    gated = fomo.list_event_interest_matches(c)
    assert gated.get("needs_fomo_ok") is True
    assert gated["matches"] == []


def test_rate_limit_and_cooldown(stores, monkeypatch):
    monkeypatch.setattr(fomo, "FOMO_MAX_NOTIFIES_PER_PHONE_PER_DAY", 1)
    a = "+13525550140"
    b = "+13525550141"
    c = "+13525550142"
    for p in (a, b, c):
        _enable(p, fomo_ok=True, sms_ok=True)

    fomo.express_event_interest(a, "evt-seed-live-jazz")
    fomo.express_event_interest(b, "evt-seed-live-jazz")
    # first pair queues notifies
    jobs1 = fomo.list_notify_queue(limit=50)["count"]
    assert jobs1 >= 1

    # third peer on same event — a/b already notified (cooldown); c may get one
    fomo.express_event_interest(c, "evt-seed-live-jazz")
    jobs = fomo.list_notify_queue(limit=100)["jobs"]
    # per-phone daily cap: no phone should have >1 sms in one day under cap=1
    from collections import Counter

    sms_by_phone = Counter(
        j["to_phone"] for j in jobs if j.get("type") == "sms" and j.get("status") == "queued"
    )
    assert all(v <= 1 for v in sms_by_phone.values())


def test_forget_clears_interests(stores):
    p = "+13525550150"
    _enable(p, fomo_ok=True, sms_ok=True)
    fomo.express_event_interest(p, "evt-seed-live-jazz")
    assert any(
        json.loads(line).get("active", True)
        for line in stores["interests"].read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    callers.forget_profile(p)
    active = [
        json.loads(line)
        for line in stores["interests"].read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("phone_e164") == p
    ]
    assert active
    assert all(not r.get("active", True) for r in active)
