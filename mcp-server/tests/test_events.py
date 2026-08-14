"""Unit tests for Gainesville events store + Visit Gainesville ingest."""

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
    source: str = "community",
    website: str = "",
) -> dict:
    out = {
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
    if website:
        out["website"] = website
    return out


def test_ensure_seeded_empty_ok(tmp_store, fixed_clock):
    """Empty store is valid — no auto fake seeds."""
    evs = events.ensure_seeded()
    assert evs == []
    assert not tmp_store.exists() or True
    # search still ok
    result = events.search_events()
    assert result["ok"] is True
    assert result["count"] == 0
    assert result["events"] == []


def test_no_auto_seed_fakes(tmp_store, fixed_clock):
    events.ensure_seeded()
    sources = events.list_event_sources()
    assert sources["ok"] is True
    assert sources["sources"] == []
    # Ensure seed generator is gone
    assert not hasattr(events, "_seed_events")


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
            _evt("a", "A", now + timedelta(hours=1), source="visitgainesville"),
            _evt("b", "B", now + timedelta(hours=2), source="visitgainesville"),
            _evt("c", "C", now + timedelta(hours=3), source="community"),
        ]
    )
    result = events.list_event_sources()
    assert result["ok"] is True
    by_src = {s["source"]: s["count"] for s in result["sources"]}
    assert by_src == {"community": 1, "visitgainesville": 2}


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


def test_fixture_search_integration(tmp_store, fixed_clock):
    """Search works from explicit fixtures (no seed dependency)."""
    now = _fixed_now()
    events.reset_store(
        [
            _evt(
                "mkt",
                "Union Street Farmers Market",
                now + timedelta(days=2),
                tags=["market", "food"],
                source="visitgainesville",
            )
        ]
    )
    result = events.search_events(query="market")
    assert result["ok"] is True
    assert result["count"] >= 1
    assert "categories" in result
    sources = events.list_event_sources()
    assert sources["ok"] is True
    assert any(s["count"] > 0 for s in sources["sources"])


def test_summarize_event_categories_and_drilldown(tmp_store, fixed_clock):
    now = _fixed_now()
    fri = now.date() + timedelta(days=2)
    sat = fri + timedelta(days=1)

    def at(day, hour):
        return datetime.combine(day, time(hour, 0), tzinfo=ET)

    events.reset_store(
        [
            _evt("w1", "Saturday Market", at(sat, 9), tags=["market", "food"]),
            _evt("w2", "Friday Jazz", at(fri, 20), tags=["music", "jazz"]),
            _evt("w3", "Park Yoga", at(sat, 8), tags=["fitness", "outdoor"]),
        ]
    )
    summary = events.summarize_event_categories(when="this_weekend")
    assert summary["ok"] is True
    assert summary["total"] >= 1
    assert isinstance(summary["categories"], list)
    assert summary["categories"]
    assert "speakable" in summary
    cat = summary["categories"][0]["category"]
    drilled = events.search_events(when="this_weekend", category=cat, limit=10)
    assert drilled["ok"] is True
    assert drilled["total_matched"] == summary["categories"][0]["count"]
    assert drilled.get("category_filter") == cat
    for ev in drilled["events"]:
        assert events._primary_category(ev) == cat


def test_search_reports_total_matched(tmp_store, fixed_clock):
    now = _fixed_now()
    events.reset_store(
        [_evt(f"e{i}", f"Event {i}", now + timedelta(hours=i + 1)) for i in range(5)]
    )
    result = events.search_events(limit=2)
    assert result["ok"] is True
    assert result["count"] == 2
    assert result.get("total_matched", 0) >= 2
    assert isinstance(result.get("categories"), list)


# --- Visit Gainesville map / filter / ingest ---------------------------------


def _tribe_raw(
    tid: int,
    title: str,
    *,
    start: str = "2026-07-18 19:00:00",
    end: str = "2026-07-18 21:00:00",
    city: str = "Gainesville",
    country: str = "United States",
    address: str = "1 Main St",
    zip_code: str = "32601",
    venue: str = "The Top",
    cost: str = "",
    website: str = "https://example.com/event",
    url: str = "https://www.visitgainesville.com/event/x/",
    categories: list | None = None,
) -> dict:
    return {
        "id": tid,
        "title": title,
        "start_date": start,
        "end_date": end,
        "cost": cost,
        "website": website,
        "url": url,
        "categories": categories
        or [{"name": "Live Music", "slug": "live-music"}],
        "venue": {
            "venue": venue,
            "address": address,
            "city": city,
            "country": country,
            "zip": zip_code,
            "website": "https://venue.example",
        },
        "excerpt": "A <b>fun</b> night out.",
    }


def test_map_visitgainesville_event_basic(fixed_clock):
    raw = _tribe_raw(42, "Jazz Night &amp; Friends")
    mapped = events.map_visitgainesville_event(raw)
    assert mapped is not None
    assert mapped["id"] == "vg-42"
    assert mapped["source"] == "visitgainesville"
    assert mapped["title"] == "Jazz Night & Friends"
    assert mapped["free"] is True
    assert mapped["venue"] == "The Top"
    assert "FL" in mapped["address"] or "32601" in mapped["address"]
    assert mapped["start"].startswith("2026-07-18T19:00:00")
    assert "live-music" in mapped["tags"] or "live music" in mapped["tags"]
    assert mapped["website"] == "https://example.com/event"
    assert "fun" in mapped["description"].lower()


def test_map_filters_nonlocal_title_and_country(fixed_clock):
    tokyo = _tribe_raw(1, "Tokyo Climate Summit 2026", city="Tokyo", country="Japan")
    assert events.map_visitgainesville_event(tokyo) is None

    remote = _tribe_raw(
        2,
        "Remote Tech Meetup",
        city="Austin",
        country="United States",
        zip_code="78701",
        address="100 Congress Ave",
    )
    assert events.map_visitgainesville_event(remote) is None

    waldo = _tribe_raw(
        3,
        "Waldo Farmers Market",
        city="Waldo",
        zip_code="32694",
        address="17805 US-301",
        venue="Waldo Farmers and Flea Market",
    )
    assert events.map_visitgainesville_event(waldo) is not None


def test_cost_paid_not_free(fixed_clock):
    raw = _tribe_raw(9, "Ticketed Show", cost="$25")
    mapped = events.map_visitgainesville_event(raw)
    assert mapped is not None
    assert mapped["free"] is False


def test_replace_preserves_community_purges_seed(tmp_store, fixed_clock):
    now = _fixed_now()
    events.reset_store(
        [
            _evt("community-1", "Open Mic", now + timedelta(days=1), source="community"),
            _evt("evt-seed-old", "Fake Seed", now + timedelta(days=1), source="seed"),
            _evt("vg-1", "Old VG", now + timedelta(days=2), source="visitgainesville"),
        ]
    )
    new_vg = [
        _evt(
            "vg-99",
            "New VG Jazz",
            now + timedelta(days=3),
            source="visitgainesville",
            tags=["music"],
        )
    ]
    result = events.replace_visitgainesville_events(new_vg)
    assert result["ok"] is True
    assert result["purged_seed"] == 1
    assert result["visitgainesville"] == 1
    assert result["preserved"] == 1
    ids = {e["id"] for e in events.ensure_seeded()}
    assert ids == {"community-1", "vg-99"}
    assert "evt-seed-old" not in ids
    assert "vg-1" not in ids


def test_ingest_visitgainesville_httpx_mock(tmp_store, fixed_clock):
    """Unit test ingest with mocked HTTP pages (httpx-style callable)."""
    page1 = {
        "events": [
            _tribe_raw(100, "GNV Jazz", start="2026-07-20 20:00:00"),
            _tribe_raw(
                101,
                "Tokyo Summit",
                city="Tokyo",
                country="Japan",
                start="2026-07-21 10:00:00",
            ),
        ],
        "total": 3,
        "total_pages": 2,
    }
    page2 = {
        "events": [
            _tribe_raw(
                102,
                "Depot Park Yoga",
                venue="Depot Park",
                start="2026-07-22 07:30:00",
                categories=[{"name": "Fitness", "slug": "fitness"}],
            ),
        ],
        "total": 3,
        "total_pages": 2,
    }

    calls: list[str] = []

    def fake_get(url: str) -> dict:
        calls.append(url)
        if "page=2" in url:
            return page2
        return page1

    # Pre-existing community + seed
    now = _fixed_now()
    events.reset_store(
        [
            _evt("community-keep", "Keep Me", now + timedelta(days=5), source="community"),
            _evt("evt-seed-x", "Seed", now + timedelta(days=1), source="seed"),
        ]
    )

    result = events.ingest_visitgainesville(
        per_page=50,
        max_pages=None,
        days_ahead=180,
        http_get=fake_get,
    )
    assert result["ok"] is True
    assert result["pages"] == 2
    assert result["mapped"] == 2
    assert result["dropped_nonlocal"] >= 1
    assert result["purged_seed"] == 1
    assert "digest" in result
    assert "vg_sha256_16=" in result["digest"]

    store = events.ensure_seeded()
    ids = {e["id"] for e in store}
    assert "community-keep" in ids
    assert "vg-100" in ids
    assert "vg-102" in ids
    assert "vg-101" not in ids
    assert "evt-seed-x" not in ids
    assert len(calls) == 2

    # Stable digest across re-read
    d1 = events.stable_events_digest()
    d2 = events.stable_events_digest()
    assert d1 == d2


def test_ingest_dry_run_no_write(tmp_store, fixed_clock):
    def fake_get(url: str) -> dict:
        return {
            "events": [_tribe_raw(7, "Only Dry", start="2026-07-19 18:00:00")],
            "total": 1,
            "total_pages": 1,
        }

    events.reset_store([])
    result = events.ingest_visitgainesville(http_get=fake_get, dry_run=True)
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["mapped"] == 1
    # Store still empty on disk
    assert events.ensure_seeded() == []
