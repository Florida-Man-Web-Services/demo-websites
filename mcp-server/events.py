"""File-backed Gainesville events store for AI 411.

search_events / get_event / list_event_sources over a JSON list of local
events. Sources: community (moderated broadcasts) and visitgainesville
(Visit Gainesville tribe REST ingest). Empty store is OK — no fake seeds.

Storage: EVENTS_PATH env (default /data/events.json; falls back to
repo data/events.json when /data is missing). Thread-safe via a lock.
Tool helpers return speakable error dicts and never raise to MCP wrappers.

Ingest: map/fetch helpers + scripts/ingest_visitgainesville_events.py
(cron-friendly digest on stdout).
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
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


SOURCE_COMMUNITY = "community"
SOURCE_VISITGAINESVILLE = "visitgainesville"
SOURCE_SEED = "seed"  # legacy only — purged on ingest, never auto-written

VG_EVENTS_API = (
    "https://www.visitgainesville.com/wp-json/tribe/events/v1/events"
)

# Alachua County + immediate satellite towns that appear on Visit Gainesville.
_LOCAL_CITIES = frozenset(
    {
        "gainesville",
        "waldo",
        "alachua",
        "high springs",
        "newberry",
        "archer",
        "hawthorne",
        "micanopy",
        "tioga",
        "jonesville",
        "melrose",
        "earleton",
        "evinston",
        "haile",
        "la crosse",
        "lacrosse",
        "cross creek",
        "island grove",
        "mcintosh",
        "orange lake",
        "williston",  # edge of area; still marketed via VG
        "brooker",
        "hampton",
        "starke",  # Bradford; occasional VG listings
    }
)

# Florida ZIP roughly 32000–34999
_FL_ZIP_RE = re.compile(r"\b3[2-4]\d{3}\b")
_NONLOCAL_TITLE_RE = re.compile(
    r"\b("
    r"tokyo|paris|london|beijing|shanghai|dubai|singapore|munich|berlin|"
    r"seoul|osaka|mumbai|delhi|sydney|melbourne|toronto summit|"
    r"las vegas convention|"
    r"worldwide virtual|global webinar"
    r")\b",
    re.IGNORECASE,
)
_US_COUNTRY_RE = re.compile(
    r"^(united states|usa|us|u\.s\.a\.?|u\.s\.?)$", re.IGNORECASE
)


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
    source = str(raw.get("source") or "").strip() or "unknown"
    out = {
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
    website = str(raw.get("website") or "").strip()
    if website:
        out["website"] = website
    return out


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
    """Load store under lock. Empty is OK — no auto fake seed events."""
    with _lock:
        return _load_events()


def load_events_unlocked() -> list[dict[str, Any]]:
    """Load without taking the lock (caller already holds _lock)."""
    return _load_events()



def _venue_dict(raw: dict[str, Any]) -> dict[str, Any]:
    venue = raw.get("venue") or {}
    if isinstance(venue, list):
        venue = venue[0] if venue else {}
    return venue if isinstance(venue, dict) else {}


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_tribe_local(value: str | None) -> datetime | None:
    """Parse tribe start_date/end_date (naive local America/New_York)."""
    if not value or not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    # Prefer explicit local "YYYY-mm-dd HH:MM:SS" (Visit Gainesville tribe).
    candidate = raw.split(".")[0].replace("T", " ", 1)
    candidate = re.split(r"[+-]\d{2}:?\d{2}$", candidate)[0].strip()
    for fmt, width in (
        ("%Y-%m-%d %H:%M:%S", 19),
        ("%Y-%m-%d %H:%M", 16),
        ("%Y-%m-%d", 10),
    ):
        try:
            dt = datetime.strptime(candidate[:width], fmt)
            return dt.replace(tzinfo=ET)
        except ValueError:
            continue
    # Fallback ISO (may include offset / Z)
    return _parse_iso(raw)


def _cost_is_free(cost: Any) -> bool:
    if cost is None:
        return True
    s = str(cost).strip()
    if not s:
        return True
    low = s.lower()
    if low in {"free", "0", "0.00", "$0", "$0.00", "none", "n/a", "na"}:
        return True
    return False


def is_local_gainesville_event(
    *,
    title: str = "",
    venue: str = "",
    address: str = "",
    city: str = "",
    country: str = "",
    zip_code: str = "",
) -> bool:
    """Drop non-local junk (foreign venues / nonsense remote titles)."""
    title_s = str(title or "")
    if _NONLOCAL_TITLE_RE.search(title_s):
        return False

    country_s = str(country or "").strip()
    if country_s and not _US_COUNTRY_RE.match(country_s):
        return False

    city_s = str(city or "").strip().lower()
    zip_s = str(zip_code or "").strip()
    address_s = str(address or "")
    venue_s = str(venue or "")
    blob = f"{venue_s} {address_s} {city_s} {zip_s}".lower()

    if _FL_ZIP_RE.search(zip_s) or _FL_ZIP_RE.search(address_s):
        # Florida ZIP — still reject if city is clearly out of metro and not FL label
        if city_s and city_s not in _LOCAL_CITIES and "fl" not in blob and "florida" not in blob:
            # ZIP says FL; allow
            return True
        return True

    if city_s:
        if city_s in _LOCAL_CITIES:
            return True
        # City present but outside known area and no FL signal
        if "florida" in blob or re.search(r"\bfl\b", blob):
            # Broader FL — keep only if Alachua-ish keywords elsewhere
            return city_s in _LOCAL_CITIES or any(
                k in blob for k in ("gainesville", "alachua", "uf ", "university of florida")
            )
        return False

    # No city: keep US/empty country with FL / Gainesville signals, else keep
    # Visit Gainesville listings with empty venue (common on some rows).
    if any(
        k in blob or k in title_s.lower()
        for k in (
            "gainesville",
            "alachua",
            "florida",
            " depot park",
            "bo diddley",
            "hippodrome",
        )
    ):
        return True
    if not venue_s and not address_s:
        return True  # VG feed without venue — allow
    # Venue only — allow if not obviously out-of-state
    bad = ("ny", "new york", "california", "texas", "chicago", "atlanta ga")
    if any(b in blob for b in bad) and "fl" not in blob and "florida" not in blob:
        return False
    return True


def map_visitgainesville_event(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Map one tribe REST event → store shape. id = vg-<tribe_id>."""
    if not isinstance(raw, dict):
        return None
    tid = raw.get("id")
    if tid is None or str(tid).strip() == "":
        return None
    title = _strip_html(str(raw.get("title") or ""))
    if not title:
        return None

    start_dt = _parse_tribe_local(str(raw.get("start_date") or ""))
    if start_dt is None:
        start_dt = _parse_iso(str(raw.get("utc_start_date") or "") + "+00:00" if raw.get("utc_start_date") else None)
    if start_dt is None:
        return None

    end_dt = _parse_tribe_local(str(raw.get("end_date") or ""))
    if end_dt is None and raw.get("utc_end_date"):
        end_dt = _parse_iso(str(raw.get("utc_end_date")) + "+00:00")

    venue_o = _venue_dict(raw)
    venue_name = _strip_html(str(venue_o.get("venue") or venue_o.get("name") or ""))
    city = _strip_html(str(venue_o.get("city") or ""))
    country = _strip_html(str(venue_o.get("country") or ""))
    zip_code = _strip_html(str(venue_o.get("zip") or venue_o.get("postal_code") or ""))
    street = _strip_html(str(venue_o.get("address") or ""))
    state = _strip_html(str(venue_o.get("state") or venue_o.get("province") or ""))
    addr_parts = [p for p in (street, city, state, zip_code) if p]
    address = ", ".join(addr_parts)
    if address and "FL" not in address.upper() and (
        city.lower() in _LOCAL_CITIES or _FL_ZIP_RE.search(zip_code)
    ):
        # Ensure FL appears for local towns missing state field
        if zip_code:
            address = f"{street}, {city}, FL {zip_code}".strip(", ")
        elif city:
            address = f"{street}, {city}, FL".strip(", ")

    if not is_local_gainesville_event(
        title=title,
        venue=venue_name,
        address=address,
        city=city,
        country=country,
        zip_code=zip_code,
    ):
        return None

    tags: list[str] = []
    cats = raw.get("categories") or []
    if isinstance(cats, list):
        for c in cats:
            if isinstance(c, dict):
                slug = str(c.get("slug") or "").strip().lower()
                name = _strip_html(str(c.get("name") or "")).lower()
                for piece in (slug, name):
                    if piece and piece not in tags:
                        tags.append(piece.replace("&amp;", "and"))
            elif c:
                tags.append(str(c).strip().lower())
    # tribe "tags" field
    extra_tags = raw.get("tags") or []
    if isinstance(extra_tags, list):
        for t in extra_tags:
            if isinstance(t, dict):
                name = _strip_html(str(t.get("name") or t.get("slug") or "")).lower()
                if name and name not in tags:
                    tags.append(name)
            elif t:
                name = str(t).strip().lower()
                if name and name not in tags:
                    tags.append(name)

    cost = raw.get("cost")
    if isinstance(raw.get("cost_details"), dict) and not cost:
        cost = raw["cost_details"].get("values") or raw["cost_details"].get("cost")
    free = _cost_is_free(cost)

    description = _strip_html(str(raw.get("excerpt") or raw.get("description") or ""))
    if len(description) > 600:
        description = description[:597].rstrip() + "..."

    url = str(raw.get("url") or "").strip()
    website = str(raw.get("website") or venue_o.get("website") or "").strip()

    return {
        "id": f"vg-{tid}",
        "title": title,
        "start": _iso(start_dt),
        "end": _iso(end_dt) if end_dt else None,
        "venue": venue_name,
        "address": address,
        "free": free,
        "tags": tags,
        "description": description,
        "url": url,
        "website": website,
        "source": SOURCE_VISITGAINESVILLE,
    }


def _default_http_get_json(url: str, timeout: float = 60.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "FloridaManWebServices-AI411-events/1.0 (+https://floridamanweb.online)",
            "Accept": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("tribe API returned non-object JSON")
    return data


def fetch_visitgainesville_events(
    *,
    per_page: int = 50,
    max_pages: int | None = None,
    start_date: str | None = None,
    http_get: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Paginate tribe events API. Returns {ok, raw_events, pages, total, error?}."""
    get = http_get or _default_http_get_json
    per_page = max(1, min(int(per_page or 50), 50))
    page = 1
    pages_fetched = 0
    raw_events: list[dict[str, Any]] = []
    total: int | None = None
    total_pages: int | None = None
    try:
        while True:
            if max_pages is not None and pages_fetched >= max_pages:
                break
            qs: dict[str, str] = {
                "per_page": str(per_page),
                "page": str(page),
            }
            if start_date:
                qs["start_date"] = start_date
            url = f"{VG_EVENTS_API}?{urllib.parse.urlencode(qs)}"
            data = get(url)
            batch = data.get("events") or []
            if not isinstance(batch, list):
                batch = []
            for item in batch:
                if isinstance(item, dict):
                    raw_events.append(item)
            pages_fetched += 1
            if total is None:
                try:
                    total = int(data.get("total")) if data.get("total") is not None else None
                except (TypeError, ValueError):
                    total = None
            if total_pages is None:
                try:
                    total_pages = (
                        int(data.get("total_pages"))
                        if data.get("total_pages") is not None
                        else None
                    )
                except (TypeError, ValueError):
                    total_pages = None
            if not batch:
                break
            if total_pages is not None and page >= total_pages:
                break
            # Safety: if API ignores page and repeats forever
            if pages_fetched > 500:
                break
            page += 1
        return {
            "ok": True,
            "raw_events": raw_events,
            "pages": pages_fetched,
            "total": total,
            "total_pages": total_pages,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "raw_events": raw_events,
            "pages": pages_fetched,
            "total": total,
            "error": f"visitgainesville fetch failed ({e.__class__.__name__}: {e})",
        }


def purge_seed_events(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Drop legacy source=seed and evt-seed-* ids. Returns (kept, purged_count)."""
    kept: list[dict[str, Any]] = []
    purged = 0
    for ev in events:
        src = str(ev.get("source") or "")
        eid = str(ev.get("id") or "")
        if src == SOURCE_SEED or eid.startswith("evt-seed-"):
            purged += 1
            continue
        kept.append(ev)
    return kept, purged


def replace_visitgainesville_events(
    mapped: list[dict[str, Any]],
) -> dict[str, Any]:
    """Replace all source=visitgainesville rows; preserve community; purge seeds.

    *mapped* should already be normalized store-shaped events with
    source=visitgainesville. Dedupes by id (last wins).
    """
    by_id: dict[str, dict[str, Any]] = {}
    for item in mapped:
        payload = dict(item)
        payload["source"] = SOURCE_VISITGAINESVILLE
        norm = _normalize_event(payload)
        if norm is None:
            continue
        by_id[norm["id"]] = norm
    vg_list = list(by_id.values())
    vg_list.sort(key=lambda e: (e.get("start") or "", e.get("id") or ""))

    with _lock:
        existing = _load_events()
        existing, purged_seed = purge_seed_events(existing)
        preserved = [
            ev
            for ev in existing
            if str(ev.get("source") or "") != SOURCE_VISITGAINESVILLE
        ]
        merged = preserved + vg_list
        try:
            _save_events(merged)
        except OSError as e:
            return {
                "ok": False,
                "error": f"could not write events store ({e.__class__.__name__})",
                "visitgainesville": len(vg_list),
                "preserved": len(preserved),
                "purged_seed": purged_seed,
            }
        return {
            "ok": True,
            "visitgainesville": len(vg_list),
            "preserved": len(preserved),
            "purged_seed": purged_seed,
            "total": len(merged),
        }


def stable_events_digest(events: list[dict[str, Any]] | None = None) -> str:
    """Cron-stable one-line digest (no timestamps)."""
    if events is None:
        with _lock:
            events = _load_events()
    counts: dict[str, int] = {}
    vg_ids: list[str] = []
    for ev in events or []:
        src = str(ev.get("source") or "unknown")
        counts[src] = counts.get(src, 0) + 1
        if src == SOURCE_VISITGAINESVILLE:
            vg_ids.append(str(ev.get("id") or ""))
    vg_ids_sorted = sorted(i for i in vg_ids if i)
    payload = "\n".join(vg_ids_sorted).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:16]
    parts = [f"total={len(events or [])}"]
    for name in sorted(counts.keys()):
        parts.append(f"{name}={counts[name]}")
    parts.append(f"vg_sha256_16={digest}")
    return " ".join(parts)


def ingest_visitgainesville(
    *,
    per_page: int = 50,
    max_pages: int | None = None,
    start_date: str | None = None,
    days_ahead: int | None = 180,
    http_get: Callable[[str], dict[str, Any]] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Fetch → map/filter → replace visitgainesville rows. Speakable result dict."""
    fetched = fetch_visitgainesville_events(
        per_page=per_page,
        max_pages=max_pages,
        start_date=start_date,
        http_get=http_get,
    )
    if not fetched.get("ok"):
        return fetched

    raw_list = fetched.get("raw_events") or []
    now = _now_et()
    horizon = None
    if days_ahead is not None and days_ahead > 0:
        horizon = now + timedelta(days=int(days_ahead))

    mapped: list[dict[str, Any]] = []
    dropped_nonlocal = 0
    dropped_horizon = 0
    dropped_invalid = 0
    for raw in raw_list:
        m = map_visitgainesville_event(raw)
        if m is None:
            # distinguish nonlocal vs invalid roughly
            if isinstance(raw, dict) and raw.get("id") and raw.get("title"):
                # try without local filter — if would map, count nonlocal
                title = _strip_html(str(raw.get("title") or ""))
                start_dt = _parse_tribe_local(str(raw.get("start_date") or ""))
                if title and start_dt is not None:
                    dropped_nonlocal += 1
                else:
                    dropped_invalid += 1
            else:
                dropped_invalid += 1
            continue
        start_dt = _parse_iso(m.get("start"))
        if start_dt is not None and start_dt < now - timedelta(hours=12):
            # skip far-past occurrences
            dropped_horizon += 1
            continue
        if horizon is not None and start_dt is not None and start_dt > horizon:
            dropped_horizon += 1
            continue
        mapped.append(m)

    if dry_run:
        # compute digest as if replaced
        with _lock:
            existing = _load_events()
        existing, purged_seed = purge_seed_events(existing)
        preserved = [
            ev
            for ev in existing
            if str(ev.get("source") or "") != SOURCE_VISITGAINESVILLE
        ]
        by_id = {m["id"]: m for m in mapped}
        merged = preserved + list(by_id.values())
        digest = stable_events_digest(merged)
        return {
            "ok": True,
            "dry_run": True,
            "fetched_raw": len(raw_list),
            "pages": fetched.get("pages"),
            "mapped": len(by_id),
            "dropped_nonlocal": dropped_nonlocal,
            "dropped_horizon": dropped_horizon,
            "dropped_invalid": dropped_invalid,
            "purged_seed": purged_seed,
            "preserved": len(preserved),
            "digest": digest,
        }

    result = replace_visitgainesville_events(mapped)
    if not result.get("ok"):
        return result
    with _lock:
        digest = stable_events_digest(_load_events())
    result.update(
        {
            "fetched_raw": len(raw_list),
            "pages": fetched.get("pages"),
            "mapped": len(mapped),
            "dropped_nonlocal": dropped_nonlocal,
            "dropped_horizon": dropped_horizon,
            "dropped_invalid": dropped_invalid,
            "digest": digest,
        }
    )
    return result


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
    category: str = "",
) -> dict[str, Any]:
    """Search local events. Returns {ok, count, events} or speakable error."""
    try:
        when_key = (when or "").strip().lower()
        if when_key not in _WHEN_VALUES:
            return {
                "ok": False,
                "count": 0,
                "total_matched": 0,
                "events": [],
                "categories": [],
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

        cat_key = (category or "").strip().lower()

        now = _now_et()
        window = _when_window(when_key, now)
        if window is None:
            return {
                "ok": False,
                "count": 0,
                "total_matched": 0,
                "events": [],
                "categories": [],
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
            if cat_key and _primary_category(ev) != cat_key:
                continue
            if not _keyword_match(ev, query or ""):
                continue
            matched.append(ev)

        matched.sort(key=lambda e: e.get("start") or "")
        limited = matched[:lim]
        cats = _category_breakdown(matched)
        return {
            "ok": True,
            "count": len(limited),
            "total_matched": len(matched),
            "events": limited,
            "categories": cats,
            "long_list": len(matched) > 3,
            "category_filter": cat_key or None,
        }
    except Exception as e:  # noqa: BLE001 — speakable, never raise
        return {
            "ok": False,
            "count": 0,
            "total_matched": 0,
            "events": [],
            "categories": [],
            "error": f"events search unavailable ({e.__class__.__name__})",
        }


# Friendly buckets for voice. First matching tag wins; else "other".
_CATEGORY_RULES: list[tuple[str, frozenset[str]]] = [
    ("music", frozenset({"music", "jazz", "live music", "concert"})),
    ("food", frozenset({"food", "market", "farmers market", "dining"})),
    ("arts", frozenset({"art", "film", "gallery", "theater", "theatre"})),
    ("sports", frozenset({"sports", "gators", "fitness"})),
    ("outdoors", frozenset({"outdoor", "outdoors", "nature", "astronomy"})),
    ("nightlife", frozenset({"nightlife", "comedy", "trivia", "bar"})),
    ("family", frozenset({"family", "kids", "children"})),
    ("free", frozenset({"free"})),
]


def _primary_category(ev: dict[str, Any]) -> str:
    tags = [str(t).strip().lower() for t in (ev.get("tags") or []) if str(t).strip()]
    for name, keys in _CATEGORY_RULES:
        if name == "free":
            continue  # free is a filter, not a primary bucket when other tags exist
        if any(t in keys for t in tags):
            return name
    if ev.get("free") and not tags:
        return "free"
    if tags:
        return tags[0]
    return "other"


def _category_breakdown(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for ev in events:
        cat = _primary_category(ev)
        buckets.setdefault(cat, []).append(ev)
    out: list[dict[str, Any]] = []
    for name, items in sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        samples = [str(e.get("title") or "").strip() for e in items[:2]]
        samples = [s for s in samples if s]
        out.append(
            {
                "category": name,
                "count": len(items),
                "sample_titles": samples,
            }
        )
    return out


def summarize_event_categories(
    query: str = "",
    when: str = "",
    free_only: bool = False,
) -> dict[str, Any]:
    """Category counts for a time window — voice browse before listing events.

    Returns total + per-category counts (and sample titles). Does not return
    the full event list; use search_events with tags= after the caller picks.
    """
    try:
        # Reuse search with high limit to get full match + categories.
        result = search_events(
            query=query,
            when=when,
            tags=None,
            free_only=free_only,
            limit=50,
        )
        if not result.get("ok"):
            return result
        total = int(result.get("total_matched") or result.get("count") or 0)
        cats = list(result.get("categories") or [])
        # Speakable one-liner for the model.
        if total == 0:
            speak = "No events matched that window."
        elif total <= 3:
            speak = (
                f"{total} event{'s' if total != 1 else ''} matched — short enough "
                "to name them directly after confirming interest."
            )
        else:
            bits = [f"{c['count']} {c['category']}" for c in cats]
            speak = (
                f"{total} events total: " + ", ".join(bits) + ". "
                "Ask which category they want details on."
            )
        return {
            "ok": True,
            "total": total,
            "when": (when or "").strip() or "upcoming",
            "query": query or "",
            "categories": cats,
            "long_list": total > 3,
            "speakable": speak,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "total": 0,
            "categories": [],
            "error": f"event categories unavailable ({e.__class__.__name__})",
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
