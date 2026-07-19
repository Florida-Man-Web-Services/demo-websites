"""Unit tests for moderated broadcast store (#50 MVP)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

import broadcasts as bc


@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    store = tmp_path / "broadcasts.jsonl"
    monkeypatch.setattr(bc, "BROADCASTS_PATH", store)
    monkeypatch.delenv("BROADCASTS_PATH", raising=False)
    return store


def test_normalize_phone_us():
    assert bc._normalize_phone("352-555-0100") == "+13525550100"
    assert bc._normalize_phone("+1 (352) 555-0100") == "+13525550100"
    assert bc._normalize_phone("13525550100") == "+13525550100"
    assert bc._normalize_phone("") is None
    assert bc._normalize_phone("123") is None


def test_submit_event_auto_approves(tmp_store):
    result = bc.submit_event_broadcast(
        title="Acoustic Open Mic",
        when_start="2026-07-20T20:00:00-04:00",
        venue="The Atlantic",
        phone="3525550100",
        free=True,
        tags="music, free",
        text="Bring your own guitar",
    )
    assert result["submitted"] is True
    assert result["status"] == "approved"
    assert result["id"].startswith("bc-")
    assert result["type"] == "event"

    lines = tmp_store.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["title"] == "Acoustic Open Mic"
    assert row["author_phone_e164"] == "+13525550100"
    assert row["status"] == "approved"
    assert row["free"] is True
    assert "music" in row["tags"]


def test_submit_notice_defaults_expiry(tmp_store):
    result = bc.submit_notice_broadcast(
        text="New taco pop-up on 13th this weekend",
        category="food",
        phone="+13525550100",
    )
    assert result["submitted"] is True
    assert result["status"] == "approved"
    assert result["broadcast"]["category"] == "food"
    assert result["broadcast"]["expires_at"]

    listed = bc.list_recent_broadcasts(category="food")
    assert listed["ok"] is True
    assert listed["count"] == 1
    assert "taco" in listed["broadcasts"][0]["text"]


def test_submit_notice_rejects_long_text(tmp_store):
    result = bc.submit_notice_broadcast(
        text="x" * 281,
        category="general",
        phone="3525550100",
    )
    assert result["submitted"] is False
    assert "280" in result["error"]
    assert not tmp_store.exists()


def test_submit_notice_bad_category(tmp_store):
    result = bc.submit_notice_broadcast(
        text="hello",
        category="spam",
        phone="3525550100",
    )
    assert result["submitted"] is False
    assert "Category" in result["error"]


def test_submit_requires_phone_and_fields(tmp_store):
    assert bc.submit_event_broadcast(
        title="x", when_start="2026-01-01T12:00:00Z", venue="y", phone="bad"
    )["submitted"] is False
    assert bc.submit_event_broadcast(
        title="", when_start="2026-01-01T12:00:00Z", venue="y", phone="3525550100"
    )["submitted"] is False
    assert bc.submit_event_broadcast(
        title="x", when_start="not-a-date", venue="y", phone="3525550100"
    )["submitted"] is False
    assert bc.submit_event_broadcast(
        title="x", when_start="2026-01-01T12:00:00Z", venue="", phone="3525550100"
    )["submitted"] is False
    assert not tmp_store.exists()


def test_blocklist_rejects(tmp_store):
    result = bc.submit_notice_broadcast(
        text="this contains blocklisttestword which is banned",
        category="general",
        phone="3525550100",
    )
    assert result["submitted"] is False
    assert result.get("status") == "rejected"
    assert not tmp_store.exists()


def test_rate_limit_per_phone_per_day(tmp_store, monkeypatch):
    monkeypatch.setattr(bc, "MAX_POSTS_PER_PHONE_PER_DAY", 2)
    phone = "3525550199"
    for i in range(2):
        r = bc.submit_notice_broadcast(
            text=f"notice number {i}",
            category="tips",
            phone=phone,
        )
        assert r["submitted"] is True
    blocked = bc.submit_notice_broadcast(
        text="one too many",
        category="tips",
        phone=phone,
    )
    assert blocked["submitted"] is False
    assert blocked.get("rate_limited") is True
    assert "limit" in blocked["error"].lower()

    # Different phone still works
    other = bc.submit_notice_broadcast(
        text="from someone else",
        category="tips",
        phone="3525550188",
    )
    assert other["submitted"] is True


def test_list_only_approved_not_expired(tmp_store):
    ok = bc.submit_notice_broadcast(
        text="live notice",
        category="music",
        phone="3525550100",
        expires_at=(
            datetime.now(timezone.utc) + timedelta(days=3)
        ).isoformat(),
    )
    assert ok["submitted"]

    # Manually append expired + rejected rows
    expired = {
        "id": "bc-expired001",
        "type": "notice",
        "status": "approved",
        "author_phone_e164": "+13525550100",
        "text": "old gossip",
        "category": "general",
        "expires_at": (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat(),
        "created_at": "2020-01-01T00:00:00+00:00",
        "updated_at": "2020-01-01T00:00:00+00:00",
        "reports": [],
        "report_count": 0,
    }
    rejected = {
        "id": "bc-rejected01",
        "type": "notice",
        "status": "rejected",
        "author_phone_e164": "+13525550100",
        "text": "bad",
        "category": "general",
        "expires_at": (
            datetime.now(timezone.utc) + timedelta(days=3)
        ).isoformat(),
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "reports": [],
        "report_count": 0,
    }
    with open(tmp_store, "a", encoding="utf-8") as f:
        f.write(json.dumps(expired) + "\n")
        f.write(json.dumps(rejected) + "\n")

    listed = bc.list_recent_broadcasts()
    assert listed["ok"] is True
    ids = {b["id"] for b in listed["broadcasts"]}
    assert ok["id"] in ids
    assert "bc-expired001" not in ids
    assert "bc-rejected01" not in ids


def test_list_category_event_filter(tmp_store):
    bc.submit_event_broadcast(
        title="Park Jam",
        when_start="2026-08-01T18:00:00-04:00",
        venue="Depot Park",
        phone="3525550100",
    )
    bc.submit_notice_broadcast(
        text="try the new coffee cart",
        category="food",
        phone="3525550100",
    )
    events = bc.list_recent_broadcasts(category="event")
    assert events["count"] == 1
    assert events["broadcasts"][0]["type"] == "event"
    food = bc.list_recent_broadcasts(category="food")
    assert food["count"] == 1
    assert food["broadcasts"][0]["type"] == "notice"


def test_report_broadcast(tmp_store):
    created = bc.submit_notice_broadcast(
        text="something questionable",
        category="general",
        phone="3525550100",
    )
    bid = created["id"]
    rep = bc.report_broadcast(bid, reason="spam ad", reporter_phone="3525550999")
    assert rep["reported"] is True
    assert rep["status"] == "reported"

    listed = bc.list_recent_broadcasts()
    assert listed["count"] == 0

    missing = bc.report_broadcast("bc-nope", reason="x")
    assert missing["reported"] is False


def test_delete_own_broadcast(tmp_store):
    created = bc.submit_event_broadcast(
        title="House Show",
        when_start="2026-09-01T21:00:00-04:00",
        venue="Private",
        phone="352-555-0100",
    )
    bid = created["id"]

    wrong = bc.delete_own_broadcast(bid, phone="3525550999")
    assert wrong["deleted"] is False

    ok = bc.delete_own_broadcast(bid, phone="3525550100")
    assert ok["deleted"] is True
    assert ok["status"] == "deleted"

    again = bc.delete_own_broadcast(bid, phone="3525550100")
    assert again["deleted"] is True
    assert again.get("already_deleted") is True

    listed = bc.list_recent_broadcasts()
    assert listed["count"] == 0


def test_env_path_override(tmp_path, monkeypatch):
    env_store = tmp_path / "via-env.jsonl"
    monkeypatch.setenv("BROADCASTS_PATH", str(env_store))
    r = bc.submit_notice_broadcast(
        text="via env path",
        category="tips",
        phone="3525550100",
    )
    assert r["submitted"] is True
    assert env_store.exists()
    assert env_store.read_text(encoding="utf-8").count("\n") == 1


# --- #48/#50 integration: approved event broadcasts → events.search_events ---


@pytest.fixture
def dual_stores(tmp_path, monkeypatch):
    """Isolated broadcasts.jsonl + events.json for cross-module tests."""
    import events as ev

    bc_path = tmp_path / "broadcasts.jsonl"
    ev_path = tmp_path / "events.json"
    monkeypatch.setattr(bc, "BROADCASTS_PATH", bc_path)
    monkeypatch.delenv("BROADCASTS_PATH", raising=False)
    monkeypatch.setenv("EVENTS_PATH", str(ev_path))
    monkeypatch.setattr(ev, "EVENTS_PATH", ev_path)
    # Fixed clock so search_events does not drop our future-dated posts.
    from datetime import datetime
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    fixed = datetime(2026, 7, 15, 14, 0, 0, tzinfo=et)
    monkeypatch.setattr(ev, "_now_et", lambda: fixed)
    # Empty events store (no seed) so community hits are easy to assert.
    ev.reset_store([])
    return {"broadcasts": bc_path, "events": ev_path, "now": fixed}


def test_approved_event_broadcast_appears_in_search_events(dual_stores):
    import events as ev

    result = bc.submit_event_broadcast(
        title="Unique Acoustic Jam Session XYZ",
        when_start="2026-07-20T20:00:00-04:00",
        when_end="2026-07-20T23:00:00-04:00",
        venue="The Atlantic",
        phone="3525550100",
        free=True,
        tags=["music", "free"],
        text="Bring your own guitar",
        url="https://example.com/jam",
    )
    assert result["submitted"] is True
    assert result["status"] == "approved"
    assert result.get("event_id") == f"community-{result['id']}"
    assert "warning" not in result

    found = ev.search_events(query="Acoustic Jam XYZ")
    assert found["ok"] is True
    assert found["count"] >= 1
    hit = next(e for e in found["events"] if "XYZ" in e["title"])
    assert hit["source"] == "community"
    assert hit["id"] == result["event_id"]
    assert hit["venue"] == "The Atlantic"
    assert hit["free"] is True
    assert "music" in hit["tags"]
    assert hit["description"] == "Bring your own guitar"
    assert hit["url"] == "https://example.com/jam"

    by_id = ev.get_event(result["event_id"])
    assert by_id["found"] is True
    assert by_id["event"]["title"] == "Unique Acoustic Jam Session XYZ"


def test_event_broadcast_respects_free_only_and_when_filters(dual_stores):
    import events as ev
    from datetime import datetime, time, timedelta
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    # Fixed now is Wed 2026-07-15 14:00 ET
    tonight_start = datetime(2026, 7, 15, 20, 0, 0, tzinfo=et).isoformat()
    paid_start = datetime(2026, 7, 22, 19, 0, 0, tzinfo=et).isoformat()

    free_r = bc.submit_event_broadcast(
        title="Free Park Concert Tonight",
        when_start=tonight_start,
        venue="Depot Park",
        phone="3525550101",
        free=True,
        tags=["music"],
    )
    paid_r = bc.submit_event_broadcast(
        title="Ticketed Club Show Later",
        when_start=paid_start,
        venue="The Wooly",
        phone="3525550102",
        free=False,
        tags=["music"],
    )
    assert free_r["submitted"] and paid_r["submitted"]

    free_only = ev.search_events(free_only=True)
    free_ids = {e["id"] for e in free_only["events"]}
    assert free_r["event_id"] in free_ids
    assert paid_r["event_id"] not in free_ids

    # Paid event still searchable without free_only
    paid_hit = ev.search_events(query="Ticketed Club")
    assert paid_r["event_id"] in {e["id"] for e in paid_hit["events"]}

    tonight = ev.search_events(when="tonight")
    tonight_ids = {e["id"] for e in tonight["events"]}
    assert free_r["event_id"] in tonight_ids
    assert paid_r["event_id"] not in tonight_ids


def test_notice_broadcast_does_not_enter_events_store(dual_stores):
    import events as ev

    notice = bc.submit_notice_broadcast(
        text="Secret notice keyword ZZXNOTICETOKEN should not be an event",
        category="general",
        phone="3525550100",
    )
    assert notice["submitted"] is True
    assert "event_id" not in notice

    search = ev.search_events(query="ZZXNOTICETOKEN")
    assert search["ok"] is True
    assert search["count"] == 0
    assert search["events"] == []


def test_ingest_community_event_upserts_stable_id(tmp_path, monkeypatch):
    import events as ev

    path = tmp_path / "events.json"
    monkeypatch.setenv("EVENTS_PATH", str(path))
    monkeypatch.setattr(ev, "EVENTS_PATH", path)
    ev.reset_store([])

    first = ev.ingest_community_event(
        broadcast_id="bc-abc123",
        title="First Title",
        when_start="2026-08-01T18:00:00-04:00",
        venue="Park",
        free=True,
        tags=["outdoor"],
        description="hello",
    )
    assert first["ok"] is True
    assert first["event_id"] == "community-bc-abc123"
    assert first["replaced"] is False

    second = ev.ingest_community_event(
        broadcast_id="bc-abc123",
        title="Updated Title",
        when_start="2026-08-01T19:00:00-04:00",
        venue="Park 2",
        free=False,
    )
    assert second["ok"] is True
    assert second["replaced"] is True
    got = ev.get_event("community-bc-abc123")
    assert got["found"] is True
    assert got["event"]["title"] == "Updated Title"
    assert got["event"]["source"] == "community"
    assert got["event"]["free"] is False