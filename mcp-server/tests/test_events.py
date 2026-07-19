"""Unit tests for Gainesville events store (#48 MVP)."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

import events

ET = ZoneInfo("America/New_York")


@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    path = tmp_path / "events.json"
    monkeypatch.setenv("EVENTS_PATH", str(path))
    monkeypatch.setattr(events, "EVENTS_PATH", path)
    return path


def _fixed_now():
    # Wednesday 2026-07-15 14:00 ET — midweek afternoon for stable windows.
    return datetime(2026, 7, 15, 14, 0, 0, tzinfo=ET)


@pytest.fixture
def fixed_clock(monkeypatch):
    monkeypatch.setattr(events, "_now_et", _fixed_now)
    return _fixed_now()


def _evt(
    eid: str,
    title: str,
    start: datetime,
    *,
    end: datetime | None = None,
    venue: str = "Test Venue",
    free: bool = True,
    tags: list[str] | None = None,
    description: str = "desc",
    source: str = "seed",
) -> dict:
    return {
        "id": eid,
        "title": title,
        "start": start.astimezone(ET).replace(microsecond=0).isoformat(),
        "end": (
            end.astimezone(ET).replace(microsecond=0).isoformat() if end else None
        ),
        "venue": venue,
        "address": "Gainesville, FL",
        "free": free,
        "tags": tags or [],
        "description": description,
        "url": "",
        "source": source,
    }


def test_ensure_seeded_writes_file(tmp_store, fixed_clock):
    evs = events.ensure_seeded()
    assert len(evs) >= 5
    assert tmp_store.exists()
    again = events.ensure_seeded()
    assert len(again) == len(evs)


def test_search_all_future_drops_expired(tmp_store, fixed_clock, monkeypatch):
    now = _fixed_now()
    past = _evt(
        "past",
        "Old Show",
        now - timedelta(days=2),
        end=now - timedelta(days=2, hours=-2),
    )
    future = _evt(
        "future",
        "Jazz Night",
        now + timedelta(hours=6),
        end=now + timedelta(hours=9),
        tags=["music", "jazz"],
        free=False,
        source="community",
    )
    events.reset_store([past, future])
    result = events.search_events()
    assert result["ok"] is True
    ids = {e["id"] for e in result["events"]}
    assert "future" in ids
    assert "past" not in ids


def test_search_query_keyword(tmp_store, fixed_clock):
    now = _fixed_now()
    events.reset_store(
        [
            _evt(
                "a",
                "Live Jazz at The Top",
                now + timedelta(hours=5),
                tags=["music", "jazz"],
                description="local trio",
                venue="The Top",
            ),
            _evt(
                "b",
                "Farmers Market",
                now + timedelta(days=2),
                tags=["market", "food"],
                description="produce and crafts",
                venue="Bo Diddley Plaza",
            ),
        ]
    )
    hit = events.search_events(query="jazz")
    assert hit["ok"] is True
    assert hit["count"] == 1
    assert hit["events"][0]["id"] == "a"

    hit2 = events.search_events(query="diddley")
    assert hit2["count"] == 1
    assert hit2["events"][0]["id"] == "b"


def test_search_free_only_and_tags(tmp_store, fixed_clock):
    now = _fixed_now()
    events.reset_store(
        [
            _evt(
                "free-music",
                "Free Concert",
                now + timedelta(hours=3),
                free=True,
                tags=["music", "outdoor"],
            ),
            _evt(
                "paid-music",
                "Ticketed Concert",
                now + timedelta(hours=4),
                free=False,
                tags=["music"],
            ),
            _evt(
                "free-food",
                "Food Festival",
                now + timedelta(hours=5),
                free=True,
                tags=["food", "outdoor"],
            ),
        ]
    )
    free = events.search_events(free_only=True)
    assert free["ok"] is True
    assert {e["id"] for e in free["events"]} == {"free-music", "free-food"}

    tagged = events.search_events(tags=["music", "outdoor"])
    assert {e["id"] for e in tagged["events"]} == {"free-music"}

    both = events.search_events(free_only=True, tags=["music"])
    assert {e["id"] for e in both["events"]} == {"free-music"}


def test_search_when_tonight_tomorrow_weekend(tmp_store, fixed_clock):
    now = _fixed_now()  # Wed 2026-07-15 14:00
    today = now.date()
    fri = today + timedelta(days=2)  # 2026-07-17
    sat = today + timedelta(days=3)

    def at(day, hour):
        return datetime.combine(day, time(hour, 0), tzinfo=ET)

    events.reset_store(
        [
            _evt("tonight", "Tonight Jazz", at(today, 20), end=at(today, 23)),
            _evt(
                "tomorrow",
                "Tomorrow Comedy",
                at(today + timedelta(days=1), 19),
                end=at(today + timedelta(days=1), 21),
            ),
            _evt("weekend", "Saturday Market", at(sat, 9), end=at(sat, 13)),
            _evt(
                "next-week",
                "Next Week Thing",
                at(today + timedelta(days=8), 18),
            ),
            # Friday evening — weekend window
            _evt("fri-night", "Art Walk", at(fri, 18), end=at(fri, 21)),
        ]
    )

    tonight = events.search_events(when="tonight")
    assert tonight["ok"] is True
    assert {e["id"] for e in tonight["events"]} == {"tonight"}

    tomorrow = events.search_events(when="tomorrow")
    assert {e["id"] for e in tomorrow["events"]} == {"tomorrow"}

    weekend = events.search_events(when="this_weekend")
    ids = {e["id"] for e in weekend["events"]}
    assert "weekend" in ids
    assert "fri-night" in ids
    assert "next-week" not in ids
    assert "tonight" not in ids


def test_search_invalid_when(tmp_store, fixed_clock):
    events.reset_store(
        [_evt("x", "X", _fixed_now() + timedelta(hours=2))]
    )
    bad = events.search_events(when="next_month")
    assert bad["ok"] is False
    assert "error" in bad
    assert bad["events"] == []


def test_get_event(tmp_store, fixed_clock):
    now = _fixed_now()
    events.reset_store(
        [
            _evt(
                "evt-1",
                "Sample",
                now + timedelta(days=1),
                description="full details",
                tags=["a"],
            )
        ]
    )
    hit = events.get_event("evt-1")
    assert hit["found"] is True
    assert hit["event"]["title"] == "Sample"
    assert hit["event"]["description"] == "full details"

    miss = events.get_event("nope")
    assert miss["found"] is False
    assert miss.get("id") == "nope"

    empty = events.get_event("")
    assert empty["found"] is False
    assert "error" in empty


def test_list_event_sources(tmp_store, fixed_clock):
    now = _fixed_now()
    events.reset_store(
        [
            _evt("a", "A", now + timedelta(hours=1), source="seed"),
            _evt("b", "B", now + timedelta(hours=2), source="seed"),
            _evt("c", "C", now + timedelta(hours=3), source="community"),
        ]
    )
    result = events.list_event_sources()
    assert result["ok"] is True
    by_src = {s["source"]: s["count"] for s in result["sources"]}
    assert by_src == {"community": 1, "seed": 2}


def test_upsert_event(tmp_store, fixed_clock):
    now = _fixed_now()
    created = events.upsert_event(
        _evt("u1", "First", now + timedelta(hours=2), source="community")
    )
    assert created["ok"] is True
    assert created["replaced"] is False

    updated = events.upsert_event(
        _evt("u1", "Updated Title", now + timedelta(hours=3), source="community")
    )
    assert updated["ok"] is True
    assert updated["replaced"] is True
    assert events.get_event("u1")["event"]["title"] == "Updated Title"


def test_limit(tmp_store, fixed_clock):
    now = _fixed_now()
    batch = [
        _evt(f"e{i}", f"Event {i}", now + timedelta(hours=i + 1))
        for i in range(5)
    ]
    events.reset_store(batch)
    result = events.search_events(limit=2)
    assert result["ok"] is True
    assert result["count"] == 2
    assert len(result["events"]) == 2


def test_seed_search_integration(tmp_store, fixed_clock):
    """Default seed is searchable without manual upsert."""
    result = events.search_events(query="market")
    assert result["ok"] is True
    assert result["count"] >= 1
    sources = events.list_event_sources()
    assert sources["ok"] is True
    assert any(s["count"] > 0 for s in sources["sources"])
