"""Evergreen Gainesville activities for AI 411 (separate from dated events).

search_activities over ACTIVITIES_PATH. Empty store is OK. No fake seeds.
Flags default off: AI411_EVERGREEN_SEARCH_ENABLED, AI411_EVERGREEN_INGEST_ENABLED.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

_DEFAULT_STORE = Path(__file__).resolve().parent.parent / "data" / "activities.json"
_DEFAULT_ALLOWLIST = Path(__file__).resolve().parent / "activities_allowlist.json"
ACTIVITIES_PATH = Path(os.getenv("ACTIVITIES_PATH", "/data/activities.json"))
FRESH_DAYS = 30
KIND = "evergreen_activity"
SOURCE_VG = "visitgainesville"
SOURCE_KIND_WP = "wp_pages"

_lock = threading.Lock()
_HTML_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def search_enabled() -> bool:
    return _flag("AI411_EVERGREEN_SEARCH_ENABLED")


def ingest_enabled() -> bool:
    return _flag("AI411_EVERGREEN_INGEST_ENABLED")


def _now() -> datetime:
    return datetime.now(ET)


def _iso(dt: datetime) -> str:
    return dt.astimezone(ET).replace(microsecond=0).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ET)
    return dt.astimezone(ET)


def _store_path() -> Path:
    env = os.getenv("ACTIVITIES_PATH")
    if env:
        return Path(env)
    path = Path(ACTIVITIES_PATH)
    if path == Path("/data/activities.json") and not path.parent.exists():
        return _DEFAULT_STORE
    return path


def _allowlist_path() -> Path:
    env = os.getenv("ACTIVITIES_ALLOWLIST_PATH")
    if env:
        return Path(env)
    return _DEFAULT_ALLOWLIST


def _strip_html(value: str) -> str:
    text = _HTML_RE.sub(" ", value or "")
    return _SPACE_RE.sub(" ", text).strip()


def _load() -> list[dict[str, Any]]:
    path = _store_path()
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict):
        data = data.get("activities") or []
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict)]


def _save(rows: list[dict[str, Any]]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(path)


def _fresh(row: dict[str, Any], now: datetime) -> bool:
    verified = _parse_iso(str(row.get("last_verified_at") or ""))
    if verified is None:
        return False
    return (now - verified) <= timedelta(days=FRESH_DAYS)


def _eligible(row: dict[str, Any], now: datetime) -> bool:
    if str(row.get("status") or "") != "published":
        return False
    if str(row.get("kind") or "") != KIND:
        return False
    return _fresh(row, now)


def _normalize(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    eid = str(raw.get("id") or "").strip()
    title = str(raw.get("title") or "").strip()
    if not eid or not title:
        return None
    status = str(raw.get("status") or "draft").strip().lower()
    if status not in {"draft", "published", "unpublished"}:
        status = "draft"
    out: dict[str, Any] = {
        "id": eid,
        "title": title,
        "kind": KIND,
        "source": str(raw.get("source") or SOURCE_VG).strip() or SOURCE_VG,
        "source_kind": str(raw.get("source_kind") or SOURCE_KIND_WP).strip() or SOURCE_KIND_WP,
        "source_url": str(raw.get("source_url") or "").strip(),
        "source_page_id": raw.get("source_page_id"),
        "fetched_at": str(raw.get("fetched_at") or "").strip(),
        "last_verified_at": str(raw.get("last_verified_at") or "").strip(),
        "status": status,
    }
    for key in ("description", "venue", "address", "website", "category"):
        val = str(raw.get(key) or "").strip()
        if val:
            out[key] = val
    tags = raw.get("tags") or []
    if isinstance(tags, list):
        cleaned = [str(t).strip().lower() for t in tags if str(t).strip()]
        if cleaned:
            out["tags"] = cleaned
    if isinstance(raw.get("free"), bool):
        out["free"] = raw["free"]
    return out


def search_activities(
    query: str = "",
    tags: list[str] | None = None,
    free_only: bool = False,
    limit: int = 10,
    category: str = "",
    source: str = "",
) -> dict[str, Any]:
    """Search published, fresh evergreen activities. Fail closed when disabled."""
    if not search_enabled():
        return {
            "ok": True,
            "count": 0,
            "activities": [],
            "disabled": True,
        }
    try:
        now = _now()
        try:
            lim = max(1, min(int(limit), 50))
        except (TypeError, ValueError):
            lim = 10
        q = (query or "").strip().lower()
        cat = (category or "").strip().lower()
        src = (source or "").strip().lower()
        tag_list: list[str] = []
        if isinstance(tags, str):
            tag_list = [t.strip().lower() for t in tags.split(",") if t.strip()]
        elif isinstance(tags, list):
            tag_list = [str(t).strip().lower() for t in tags if str(t).strip()]
        with _lock:
            rows = [_normalize(r) for r in _load()]
        matched: list[dict[str, Any]] = []
        for row in rows:
            if row is None or not _eligible(row, now):
                continue
            if src and str(row.get("source") or "").lower() != src:
                continue
            if cat and str(row.get("category") or "").lower() != cat:
                continue
            if free_only and row.get("free") is not True:
                continue
            if tag_list:
                have = {str(t).lower() for t in (row.get("tags") or [])}
                if not set(tag_list).issubset(have):
                    continue
            if q:
                blob = " ".join(
                    str(row.get(k) or "")
                    for k in ("title", "description", "venue", "category")
                ).lower()
                if q not in blob:
                    continue
            public = {
                k: row[k]
                for k in (
                    "id",
                    "title",
                    "kind",
                    "source",
                    "source_url",
                    "last_verified_at",
                    "description",
                    "venue",
                    "address",
                    "website",
                    "tags",
                    "category",
                    "free",
                )
                if k in row
            }
            matched.append(public)
        return {
            "ok": True,
            "count": len(matched[:lim]),
            "total_matched": len(matched),
            "activities": matched[:lim],
            "disabled": False,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "count": 0,
            "activities": [],
            "error": f"activities unavailable ({e.__class__.__name__})",
        }


def load_allowlist() -> dict[str, Any]:
    path = _allowlist_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"pages": []}
    if not isinstance(data, dict):
        return {"pages": []}
    pages = data.get("pages")
    if not isinstance(pages, list):
        pages = []
    data["pages"] = [p for p in pages if isinstance(p, dict)]
    return data


def map_wp_page(raw: dict[str, Any], *, allow: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    """Map a WP REST page to an activity. Hub pages and unpublished allowlist stay draft."""
    if not isinstance(raw, dict):
        return None
    try:
        raw_id = raw.get("id")
        if raw_id is None:
            return None
        page_id = int(raw_id)
    except (TypeError, ValueError):
        return None
    title_raw = raw.get("title")
    if isinstance(title_raw, dict):
        title = _strip_html(str(title_raw.get("rendered") or ""))
    else:
        title = _strip_html(str(title_raw or ""))
    if not title:
        return None
    link = str(raw.get("link") or raw.get("source_url") or "").strip()
    excerpt = _strip_html(str((raw.get("excerpt") or {}).get("rendered") or raw.get("excerpt") or ""))
    iso = _iso(now)
    status = "published" if allow.get("publish") is True and not allow.get("hub") else "draft"
    row: dict[str, Any] = {
        "id": f"vg-page-{page_id}",
        "title": title,
        "kind": KIND,
        "source": SOURCE_VG,
        "source_kind": SOURCE_KIND_WP,
        "source_url": link,
        "source_page_id": page_id,
        "fetched_at": iso,
        "last_verified_at": iso if status == "published" else "",
        "status": status,
    }
    if excerpt:
        row["description"] = excerpt[:500]
    cat = str(allow.get("category") or "").strip().lower()
    if cat:
        row["category"] = cat
    return _normalize(row)


def ingest_visitgainesville_pages(
    http_get_json: Callable[[str], dict[str, Any] | list[Any]] | None = None,
) -> dict[str, Any]:
    """Fetch allowlisted WP pages. No-op when ingest flag is off."""
    if not ingest_enabled():
        return {"ok": True, "ingested": 0, "disabled": True}
    getter = http_get_json
    if getter is None:
        return {
            "ok": False,
            "ingested": 0,
            "error": "http getter required (no implicit live fetch in library call)",
        }
    allow = load_allowlist()
    pages = [p for p in allow.get("pages") or [] if p.get("publish") is True]
    if not pages:
        return {"ok": True, "ingested": 0, "disabled": False, "note": "no publish:true allowlist rows"}
    now = _now()
    mapped: list[dict[str, Any]] = []
    errors = 0
    for page in pages:
        try:
            pid = int(page.get("id"))
        except (TypeError, ValueError):
            errors += 1
            continue
        try:
            raw = getter(
                f"https://www.visitgainesville.com/wp-json/wp/v2/pages/{pid}?_fields=id,slug,link,title,excerpt"
            )
        except Exception:
            errors += 1
            continue
        if isinstance(raw, list):
            raw = raw[0] if raw else {}
        if not isinstance(raw, dict) or not raw:
            errors += 1
            continue
        row = map_wp_page(raw, allow=page, now=now)
        if row is None:
            errors += 1
            continue
        mapped.append(row)
    if not mapped and errors:
        return {"ok": False, "ingested": 0, "error": "all fetches failed; store unchanged"}
    with _lock:
        existing = [_normalize(r) for r in _load()]
        by_id = {r["id"]: r for r in existing if r}
        for row in mapped:
            by_id[row["id"]] = row
        _save(list(by_id.values()))
    return {"ok": True, "ingested": len(mapped), "errors": errors, "disabled": False}
