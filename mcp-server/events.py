"""File-backed Gainesville events store for AI 411 (#48 MVP).

search_events / get_event / list_event_sources over a JSON list of local
events. No live crawl in this slice — seed data only (source=seed|community).

Storage: EVENTS_PATH env (default /data/events.json; falls back to
repo data/events.json when /data is missing). Thread-safe via a lock.
Tool helpers return speakable error dicts and never raise to MCP wrappers.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# Monkeypatchable in tests (also settable via EVENTS_PATH env).
_DEFAULT_DATA = Path(__file__).resolve().parent.parent / "data" / "events.json"
EVENTS_PATH = Path(os.getenv("EVENTS_PATH", "/data/events.json"))

_lock = threading.Lock()

_WHEN_VALUES = frozenset({"", "tonight", "tomorrow", "this_weekend"})


def _store_path() -> Path:
    """Resolve path each call so env/monkeypatch of EVENTS_PATH works."""
    env = os.getenv("EVENTS_PATH")
    if env:
        return Path(env)
    path = Path(EVENTS_PATH)
    # Prefer explicit /data when present (container PVC); else repo data/.
    if path == Path("/data/events.json") and not path.parent.exists():
        return _DEFAULT_DATA
    return path


def _now_et() -> datetime:
    """Current time in America/New_York (mockable in tests)."""
    return datetime.now(ET)


def _parse_iso(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        # Support trailing Z.
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ET)
    return dt.astimezone(ET)


def _iso(dt: datetime) -> str:
    return dt.astimezone(ET).replace(microsecond=0).isoformat()


def _seed_events(now: datetime | None = None) -> list[dict[str, Any]]:
    """~8 fake Gainesville events relative to *now* so the store stays useful."""
    now = now or _now_et()
    today = now.date()

    def at(day: date, hour: int, minute: int = 0) -> datetime:
        return datetime.combine(day, time(hour, minute), tzinfo=ET)

    def end_of(start: datetime, hours: int = 2) -> datetime:
        return start + timedelta(hours=hours)

    # Map weekday → upcoming Friday for weekend seeds.
    # Python: Mon=0 ... Sun=6
    weekday = today.weekday()
    days_to_fri = (4 - weekday) % 7
    fri = today + timedelta(days=days_to_fri)
    sat = fri + timedelta(days=1)
    sun = fri + timedelta(days=2)
    tomorrow = today + timedelta(days=1)
    midweek = today + timedelta(days=3 if weekday < 3 else 1)

    seeds: list[dict[str, Any]] = [
        {
            "id": "evt-seed-farmers-market",
            "title": "Union Street Farmers Market",
            "start": _iso(at(sat, 8, 30)),
            "end": _iso(at(sat, 13, 0)),
            "venue": "Bo Diddley Plaza",
            "address": "111 E University Ave, Gainesville, FL",
            "free": True,
            "tags": ["market", "food", "family", "outdoor"],
            "description": (
                "Weekly farmers market with local produce, baked goods, "
                "and crafts in downtown Gainesville."
            ),
            "url": "https://example.com/union-street-market",
            "source": "seed",
        },
        {
            "id": "evt-seed-live-jazz",
            "title": "Live Jazz at The Top",
            "start": _iso(at(today, 20, 0)),
            "end": _iso(end_of(at(today, 20, 0), 3)),
            "venue": "The Top",
            "address": "30 N Main St, Gainesville, FL",
            "free": False,
            "tags": ["music", "jazz", "nightlife"],
            "description": (
                "Local jazz trio every night this week. Cover charge at the door."
            ),
            "url": "",
            "source": "seed",
        },
        {
            "id": "evt-seed-comedy",
            "title": "Open Mic Comedy Night",
            "start": _iso(at(tomorrow, 19, 30)),
            "end": _iso(end_of(at(tomorrow, 19, 30), 2)),
            "venue": "The Wooly",
            "address": "20 N Main St, Gainesville, FL",
            "free": True,
            "tags": ["comedy", "nightlife", "free"],
            "description": "Free open mic comedy. Sign up starts at 7pm.",
            "url": "",
            "source": "community",
        },
        {
            "id": "evt-seed-art-walk",
            "title": "Downtown Art Walk",
            "start": _iso(at(fri, 18, 0)),
            "end": _iso(at(fri, 21, 0)),
            "venue": "Downtown Gainesville galleries",
            "address": "University Ave & Main St, Gainesville, FL",
            "free": True,
            "tags": ["art", "outdoor", "family", "free"],
            "description": (
                "Monthly self-guided gallery hop with street performers and food trucks."
            ),
            "url": "https://example.com/art-walk",
            "source": "seed",
        },
        {
            "id": "evt-seed-gators-watch",
            "title": "Gators Watch Party",
            "start": _iso(at(sat, 15, 30)),
            "end": _iso(end_of(at(sat, 15, 30), 4)),
            "venue": "Satchel's Pizza",
            "address": "1800 NE 23rd Ave, Gainesville, FL",
            "free": True,
            "tags": ["sports", "food", "family"],
            "description": "Watch the Gators on the big screens with pizza specials.",
            "url": "",
            "source": "community",
        },
        {
            "id": "evt-seed-yoga-park",
            "title": "Sunrise Yoga in the Park",
            "start": _iso(at(sun, 7, 30)),
            "end": _iso(at(sun, 8, 30)),
            "venue": "Depot Park",
            "address": "200 SE Depot Ave, Gainesville, FL",
            "free": True,
            "tags": ["fitness", "outdoor", "free", "family"],
            "description": "All-levels outdoor yoga. Bring a mat and water.",
            "url": "",
            "source": "seed",
        },
        {
            "id": "evt-seed-trivia",
            "title": "Trivia Night at First Magnitude",
            "start": _iso(at(midweek, 19, 0)),
            "end": _iso(end_of(at(midweek, 19, 0), 2)),
            "venue": "First Magnitude Brewing",
            "address": "1220 SE Veitch St, Gainesville, FL",
            "free": True,
            "tags": ["trivia", "nightlife", "food", "free"],
            "description": "Team trivia with prizes. No cover; food trucks on site.",
            "url": "https://example.com/first-mag-trivia",
            "source": "community",
        },
        {
            "id": "evt-seed-film",
            "title": "Indie Film Screening: Florida Stories",
            "start": _iso(at(fri, 19, 0)),
            "end": _iso(end_of(at(fri, 19, 0), 2)),
            "venue": "Hippodrome Theatre",
            "address": "25 SE 2nd Pl, Gainesville, FL",
            "free": False,
            "tags": ["film", "art", "nightlife"],
            "description": "Local filmmaker shorts night. Tickets at the box office.",
            "url": "https://example.com/hipp-film",
            "source": "seed",
        },
    ]
    return seeds


def _normalize_event(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    eid = str(raw.get("id") or "").strip()
    title = str(raw.get("title") or "").strip()
    start = raw.get("start")
    if not eid or not title or not start:
        return None
    start_dt = _parse_iso(str(start))
    if start_dt is None:
        return None
    end_raw = raw.get("end")
    end_iso = None
    if end_raw:
        end_dt = _parse_iso(str(end_raw))
        if end_dt is not None:
            end_iso = _iso(end_dt)
    tags = raw.get("tags") or []
    if not isinstance(tags, list):
        tags = []
    tags = [str(t).strip().lower() for t in tags if str(t).strip()]
    free = raw.get("free")
    if not isinstance(free, bool):
        free = str(free).strip().lower() in ("1", "true", "yes", "free")
    source = str(raw.get("source") or "seed").strip() or "seed"
    return {
        "id": eid,
        "title": title,
        "start": _iso(start_dt),
        "end": end_iso,
        "venue": str(raw.get("venue") or "").strip(),
        "address": str(raw.get("address") or "").strip(),
        "free": free,
        "tags": tags,
        "description": str(raw.get("description") or "").strip(),
        "url": str(raw.get("url") or "").strip(),
        "source": source,
    }


def _load_events() -> list[dict[str, Any]]:
    path = _store_path()
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, list):
        raw_list = data
    elif isinstance(data, dict) and isinstance(data.get("events"), list):
        raw_list = data["events"]
    else:
        return []
    out: list[dict[str, Any]] = []
    for item in raw_list:
        norm = _normalize_event(item)
        if norm:
            out.append(norm)
    return out


def _save_events(events: list[dict[str, Any]]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = {"events": events}
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(path)


def ensure_seeded() -> list[dict[str, Any]]:
    """Load store; if empty, write relative seed events and return them."""
    with _lock:
        events = _load_events()
        if events:
            return events
        events = _seed_events()
        try:
            _save_events(events)
        except OSError:
            # Read-only FS — still return in-memory seed for the caller.
            pass
        return events


def upsert_event(event: dict[str, Any]) -> dict[str, Any]:
    """Insert or replace an event by id. Helper for tests/seed (not an MCP tool)."""
    norm = _normalize_event(event)
    if norm is None:
        return {
            "ok": False,
            "error": "invalid event — need id, title, and parseable start ISO",
        }
    with _lock:
        events = _load_events()
        replaced = False
        for i, existing in enumerate(events):
            if existing["id"] == norm["id"]:
                events[i] = norm
                replaced = True
                break
        if not replaced:
            events.append(norm)
        try:
            _save_events(events)
        except OSError as e:
            return {
                "ok": False,
                "error": f"could not write events store ({e.__class__.__name__})",
            }
        return {"ok": True, "event": norm, "replaced": replaced}


def ingest_community_event(
    broadcast_id: str,
    title: str,
    when_start: str,
    venue: str = "",
    when_end: str = "",
    free: bool = True,
    tags: list[str] | None = None,
    description: str = "",
    url: str = "",
) -> dict[str, Any]:
    """Upsert an approved community broadcast into the events index.

    Stable id: community-<broadcast_id>. source is always 'community'.
    Uses upsert_event (own lock) — call outside the broadcasts write lock
    to avoid nested lock ordering issues.

    v1: deleted/reported broadcasts are left in the events store; they
    expire via normal end/start time filtering in search_events.
    """
    bid = str(broadcast_id or "").strip()
    if not bid:
        return {
            "ok": False,
            "error": "broadcast_id is required to ingest a community event",
        }
    eid = bid if bid.startswith("community-") else f"community-{bid}"
    payload: dict[str, Any] = {
        "id": eid,
        "title": title,
        "start": when_start,
        "end": (when_end or "").strip() or None,
        "venue": venue or "",
        "address": "",
        "free": free,
        "tags": tags or [],
        "description": description or "",
        "url": url or "",
        "source": "community",
    }
    result = upsert_event(payload)
    if result.get("ok"):
        result["event_id"] = eid
        result["broadcast_id"] = bid
    return result


def reset_store(events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Replace entire store (tests). None → empty list."""
    with _lock:
        payload = []
        for item in events or []:
            norm = _normalize_event(item)
            if norm:
                payload.append(norm)
        try:
            _save_events(payload)
        except OSError as e:
            return {
                "ok": False,
                "error": f"could not write events store ({e.__class__.__name__})",
            }
        return {"ok": True, "count": len(payload)}


def _event_end_or_start(ev: dict[str, Any]) -> datetime | None:
    end = _parse_iso(ev.get("end"))
    if end is not None:
        return end
    return _parse_iso(ev.get("start"))


def _is_expired(ev: dict[str, Any], now: datetime) -> bool:
    boundary = _event_end_or_start(ev)
    if boundary is None:
        return True
    return boundary < now


def _when_window(
    when: str, now: datetime
) -> tuple[datetime | None, datetime | None] | None:
    """Return (window_start, window_end) inclusive-ish, or None if invalid when.

    Empty when → (now, None) meaning all future (caller still drops expired).
    """
    key = (when or "").strip().lower()
    if key not in _WHEN_VALUES:
        return None
    today = now.date()
    if key == "":
        return (now, None)
    if key == "tonight":
        # 5pm today through 4am tomorrow.
        start = datetime.combine(today, time(17, 0), tzinfo=ET)
        end = datetime.combine(today + timedelta(days=1), time(4, 0), tzinfo=ET)
        # If it's already after 4am tomorrow window and before 5pm, still
        # treat "tonight" as today's evening — clamp start to now if past.
        if now > end:
            # Past tonight's window: empty results via impossible window.
            return (now, now)
        return (max(start, now) if now > start else start, end)
    if key == "tomorrow":
        d = today + timedelta(days=1)
        start = datetime.combine(d, time(0, 0), tzinfo=ET)
        end = datetime.combine(d, time(23, 59, 59), tzinfo=ET)
        return (start, end)
    # this_weekend: Fri 00:00 – Sun 23:59:59
    weekday = today.weekday()  # Mon=0
    if weekday <= 4:
        # Mon–Fri: upcoming Friday (today if Friday)
        days_to_fri = 4 - weekday
        fri = today + timedelta(days=days_to_fri)
    else:
        # Sat/Sun: weekend already started — Friday of this week
        days_since_fri = weekday - 4
        fri = today - timedelta(days=days_since_fri)
    sun = fri + timedelta(days=2)
    start = datetime.combine(fri, time(0, 0), tzinfo=ET)
    end = datetime.combine(sun, time(23, 59, 59), tzinfo=ET)
    # If weekend start is still in the future, use that; else from now.
    window_start = start if start > now else now
    return (window_start, end)


def _event_in_window(
    ev: dict[str, Any],
    window_start: datetime | None,
    window_end: datetime | None,
) -> bool:
    start = _parse_iso(ev.get("start"))
    if start is None:
        return False
    end = _parse_iso(ev.get("end")) or start
    # Overlap: event starts before window ends AND event ends after window starts.
    if window_start is not None and end < window_start:
        return False
    if window_end is not None and start > window_end:
        return False
    return True


def _keyword_match(ev: dict[str, Any], query: str) -> bool:
    q = query.strip().lower()
    if not q:
        return True
    tokens = [t for t in re.split(r"\s+", q) if t]
    if not tokens:
        return True
    hay = " ".join(
        [
            str(ev.get("title") or ""),
            str(ev.get("description") or ""),
            str(ev.get("venue") or ""),
            " ".join(ev.get("tags") or []),
            str(ev.get("address") or ""),
        ]
    ).lower()
    # All tokens must appear somewhere (AND).
    return all(t in hay for t in tokens)


def _tags_match(ev: dict[str, Any], tags: list[str] | None) -> bool:
    if not tags:
        return True
    want = {str(t).strip().lower() for t in tags if str(t).strip()}
    if not want:
        return True
    have = {str(t).strip().lower() for t in (ev.get("tags") or [])}
    return want.issubset(have)


def search_events(
    query: str = "",
    when: str = "",
    tags: list[str] | None = None,
    free_only: bool = False,
    limit: int = 10,
) -> dict[str, Any]:
    """Search local events. Returns {ok, count, events} or speakable error."""
    try:
        when_key = (when or "").strip().lower()
        if when_key not in _WHEN_VALUES:
            return {
                "ok": False,
                "count": 0,
                "events": [],
                "error": (
                    f"unknown when {when!r} — try tonight, tomorrow, "
                    "this_weekend, or leave empty for all upcoming"
                ),
            }
        try:
            lim = int(limit)
        except (TypeError, ValueError):
            lim = 10
        lim = max(1, min(lim, 50))

        tag_list: list[str] | None
        if tags is None:
            tag_list = None
        elif isinstance(tags, str):
            # MCP may pass comma-separated string.
            tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        elif isinstance(tags, list):
            tag_list = [str(t) for t in tags]
        else:
            tag_list = None

        now = _now_et()
        window = _when_window(when_key, now)
        if window is None:
            return {
                "ok": False,
                "count": 0,
                "events": [],
                "error": f"unknown when {when!r}",
            }
        window_start, window_end = window

        events = ensure_seeded()
        matched: list[dict[str, Any]] = []
        for ev in events:
            if _is_expired(ev, now):
                continue
            if not _event_in_window(ev, window_start, window_end):
                continue
            if free_only and not ev.get("free"):
                continue
            if not _tags_match(ev, tag_list):
                continue
            if not _keyword_match(ev, query or ""):
                continue
            matched.append(ev)

        matched.sort(key=lambda e: e.get("start") or "")
        limited = matched[:lim]
        return {"ok": True, "count": len(limited), "events": limited}
    except Exception as e:  # noqa: BLE001 — speakable, never raise
        return {
            "ok": False,
            "count": 0,
            "events": [],
            "error": f"events search unavailable ({e.__class__.__name__})",
        }


def get_event(event_id: str) -> dict[str, Any]:
    """Return full event record or found:false."""
    try:
        eid = str(event_id or "").strip()
        if not eid:
            return {
                "found": False,
                "error": "event id is required",
            }
        events = ensure_seeded()
        for ev in events:
            if ev.get("id") == eid:
                return {"found": True, "event": ev}
        return {"found": False, "id": eid}
    except Exception as e:  # noqa: BLE001
        return {
            "found": False,
            "error": f"events lookup unavailable ({e.__class__.__name__})",
        }


def list_event_sources() -> dict[str, Any]:
    """List sources with event counts: {ok, sources:[{source,count}]}."""
    try:
        events = ensure_seeded()
        counts: dict[str, int] = {}
        for ev in events:
            src = str(ev.get("source") or "unknown")
            counts[src] = counts.get(src, 0) + 1
        sources = [
            {"source": name, "count": counts[name]}
            for name in sorted(counts.keys())
        ]
        return {"ok": True, "sources": sources}
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "sources": [],
            "error": f"event sources unavailable ({e.__class__.__name__})",
        }
