"""Evergreen activities store — default-off, no fake NAP."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import activities

ET = ZoneInfo("America/New_York")


def _iso(dt: datetime) -> str:
    return dt.astimezone(ET).replace(microsecond=0).isoformat()


def test_search_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("AI411_EVERGREEN_SEARCH_ENABLED", raising=False)
    monkeypatch.setenv("ACTIVITIES_PATH", str(tmp_path / "activities.json"))
    result = activities.search_activities(query="park")
    assert result["ok"] is True
    assert result["disabled"] is True
    assert result["count"] == 0
    assert result["activities"] == []


def test_search_published_fresh_and_free_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("AI411_EVERGREEN_SEARCH_ENABLED", "true")
    store = tmp_path / "activities.json"
    monkeypatch.setenv("ACTIVITIES_PATH", str(store))
    now = datetime.now(ET)
    activities._save(
        [
            {
                "id": "vg-page-1",
                "title": "Paynes Prairie",
                "kind": "evergreen_activity",
                "source": "visitgainesville",
                "source_kind": "wp_pages",
                "source_url": "https://www.visitgainesville.com/example/",
                "source_page_id": 1,
                "fetched_at": _iso(now),
                "last_verified_at": _iso(now),
                "status": "published",
                "category": "outdoors",
                "description": "Boardwalk and bison overlook.",
            },
            {
                "id": "vg-page-2",
                "title": "Stale Trail",
                "kind": "evergreen_activity",
                "source": "visitgainesville",
                "source_kind": "wp_pages",
                "source_url": "https://www.visitgainesville.com/stale/",
                "source_page_id": 2,
                "fetched_at": _iso(now - timedelta(days=40)),
                "last_verified_at": _iso(now - timedelta(days=40)),
                "status": "published",
            },
            {
                "id": "vg-page-3",
                "title": "Draft Only",
                "kind": "evergreen_activity",
                "source": "visitgainesville",
                "source_kind": "wp_pages",
                "source_url": "https://www.visitgainesville.com/draft/",
                "source_page_id": 3,
                "fetched_at": _iso(now),
                "last_verified_at": _iso(now),
                "status": "draft",
            },
        ]
    )
    hit = activities.search_activities(query="prairie")
    assert hit["ok"] is True
    assert hit["count"] == 1
    assert hit["activities"][0]["title"] == "Paynes Prairie"
    assert "address" not in hit["activities"][0]
    assert "free" not in hit["activities"][0]
    free_only = activities.search_activities(free_only=True)
    assert free_only["count"] == 0
    stale = activities.search_activities(query="stale")
    assert stale["count"] == 0
    draft = activities.search_activities(query="draft")
    assert draft["count"] == 0


def test_ingest_disabled_does_not_write(tmp_path, monkeypatch):
    monkeypatch.delenv("AI411_EVERGREEN_INGEST_ENABLED", raising=False)
    monkeypatch.setenv("ACTIVITIES_PATH", str(tmp_path / "activities.json"))
    called = {"n": 0}

    def getter(_url: str):
        called["n"] += 1
        return {"id": 1, "title": {"rendered": "Nope"}}

    result = activities.ingest_visitgainesville_pages(http_get_json=getter)
    assert result["ok"] is True
    assert result["disabled"] is True
    assert called["n"] == 0
    assert not (tmp_path / "activities.json").exists()


def test_ingest_publish_true_maps_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("AI411_EVERGREEN_INGEST_ENABLED", "1")
    monkeypatch.setenv("AI411_EVERGREEN_SEARCH_ENABLED", "1")
    monkeypatch.setenv("ACTIVITIES_PATH", str(tmp_path / "a.json"))
    allow = tmp_path / "allow.json"
    allow.write_text(
        '{"pages":[{"id":229625,"slug":"open-pickleball-nights","category":"sports","publish":true}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("ACTIVITIES_ALLOWLIST_PATH", str(allow))

    def getter(url: str):
        assert "229625" in url
        return {
            "id": 229625,
            "link": "https://www.visitgainesville.com/open-pickleball-nights/",
            "title": {"rendered": "Open Pickleball Nights"},
            "excerpt": {"rendered": "<p>Weekly open play.</p>"},
        }

    first = activities.ingest_visitgainesville_pages(http_get_json=getter)
    assert first["ok"] is True
    assert first["ingested"] == 1
    second = activities.ingest_visitgainesville_pages(http_get_json=getter)
    assert second["ok"] is True
    found = activities.search_activities(query="pickleball")
    assert found["count"] == 1
    assert found["activities"][0]["id"] == "vg-page-229625"
    assert found["activities"][0]["category"] == "sports"


def test_ingest_all_fail_leaves_store(tmp_path, monkeypatch):
    monkeypatch.setenv("AI411_EVERGREEN_INGEST_ENABLED", "true")
    store = tmp_path / "a.json"
    monkeypatch.setenv("ACTIVITIES_PATH", str(store))
    allow = tmp_path / "allow.json"
    allow.write_text('{"pages":[{"id":1,"publish":true}]}', encoding="utf-8")
    monkeypatch.setenv("ACTIVITIES_ALLOWLIST_PATH", str(allow))
    now = datetime.now(ET)
    activities._save(
        [
            {
                "id": "vg-page-keep",
                "title": "Keep Me",
                "kind": "evergreen_activity",
                "source": "visitgainesville",
                "source_kind": "wp_pages",
                "source_url": "https://example.com/keep",
                "source_page_id": 9,
                "fetched_at": _iso(now),
                "last_verified_at": _iso(now),
                "status": "published",
            }
        ]
    )

    def getter(_url: str):
        raise TimeoutError("nope")

    result = activities.ingest_visitgainesville_pages(http_get_json=getter)
    assert result["ok"] is False
    monkeypatch.setenv("AI411_EVERGREEN_SEARCH_ENABLED", "true")
    hit = activities.search_activities(query="keep")
    assert hit["count"] == 1
