"""File-backed ChangeRequest store + site outline helpers for owner updates.

JSONL store under CHANGE_REQUESTS_PATH (default: repo data/change-requests.jsonl).
Thread-safe via a module lock. Sync helpers never raise to callers of the MCP
tools — server wrappers catch unexpected errors and return speakable dicts.
"""

from __future__ import annotations

import html as html_lib
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

# Monkeypatchable in tests (also settable via CHANGE_REQUESTS_PATH env).
CHANGE_REQUESTS_PATH = Path(
    os.getenv(
        "CHANGE_REQUESTS_PATH",
        str(
            Path(__file__).resolve().parent.parent
            / "data"
            / "change-requests.jsonl"
        ),
    )
)

# Where generated-sites live; tests monkeypatch this. Falls back to repo path.
GENERATED_SITES_DIR = Path(
    os.getenv(
        "GENERATED_SITES_DIR",
        str(Path(__file__).resolve().parent.parent / "generated-sites"),
    )
)

_write_lock = threading.Lock()

OPEN_STATUSES = frozenset(
    {
        "pending",
        "needs_clarification",
        "approved",
        "in_progress",
    }
)
TERMINAL_STATUSES = frozenset(
    {
        "cancelled",
        "shipped",
        "rejected",
        "failed",
    }
)
VALID_STATUSES = OPEN_STATUSES | TERMINAL_STATUSES

VALID_ITEM_TYPES = frozenset(
    {
        "copy",
        "hours",
        "phone",
        "address",
        "menu_item",
        "service",
        "color_theme",
        "image",
        "section_add",
        "section_remove",
        "other",
    }
)

VALID_PRIORITIES = frozenset({"normal", "rush"})
VALID_SOURCES = frozenset({"voice", "sms", "mcp", "admin"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _store_path() -> Path:
    """Resolve store path each call so env/monkeypatch of CHANGE_REQUESTS_PATH works."""
    env = os.getenv("CHANGE_REQUESTS_PATH")
    if env:
        return Path(env)
    return Path(CHANGE_REQUESTS_PATH)


def _sites_dir() -> Path:
    env = os.getenv("GENERATED_SITES_DIR")
    if env:
        return Path(env)
    return Path(GENERATED_SITES_DIR)


def _read_all(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                records.append(obj)
    return records


def _write_all(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
    tmp.replace(path)


def _normalize_items(items: Any) -> tuple[list[dict[str, Any]] | None, str | None]:
    if items is None:
        return [], None
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except json.JSONDecodeError:
            return None, "items must be a list of objects (or JSON array string)"
    if not isinstance(items, list):
        return None, "items must be a list of objects"
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(items):
        if not isinstance(raw, dict):
            return None, f"items[{i}] must be an object"
        item_type = str(raw.get("type") or "other").strip().lower()
        if item_type not in VALID_ITEM_TYPES:
            item_type = "other"
        target = str(raw.get("target") or "").strip()
        after = raw.get("after")
        if after is None:
            after = ""
        else:
            after = str(after)
        entry: dict[str, Any] = {
            "type": item_type,
            "target": target,
            "after": after,
        }
        if raw.get("before") is not None:
            entry["before"] = str(raw.get("before"))
        if raw.get("notes") is not None:
            entry["notes"] = str(raw.get("notes"))
        out.append(entry)
    return out, None


def create_change_request(
    business_slug: str,
    summary: str,
    items: Any = None,
    caller_phone: str = "",
    source: str = "voice",
    confirmation_spoken: bool = True,
    priority: str = "normal",
    call_sid: str = "",
    transcript_ref: str = "",
) -> dict[str, Any]:
    """Append a new ChangeRequest with status=pending. Returns speakable result."""
    slug = (business_slug or "").strip()
    if not slug:
        return {
            "created": False,
            "error": "business_slug is required",
        }
    summary_s = (summary or "").strip()
    if not summary_s:
        return {
            "created": False,
            "error": "summary is required — give a one-line description of the change",
        }

    normalized, err = _normalize_items(items)
    if err:
        return {"created": False, "error": err}

    src = (source or "voice").strip().lower()
    if src not in VALID_SOURCES:
        src = "voice"
    pri = (priority or "normal").strip().lower()
    if pri not in VALID_PRIORITIES:
        pri = "normal"

    record: dict[str, Any] = {
        "id": f"cr-{uuid.uuid4().hex[:12]}",
        "business_slug": slug,
        "caller_phone": (caller_phone or "").strip(),
        "created_at": _now_iso(),
        "status": "pending",
        "source": src,
        "summary": summary_s,
        "items": normalized or [],
        "confirmation_spoken": bool(confirmation_spoken),
        "priority": pri,
    }
    if call_sid:
        record["call_sid"] = str(call_sid).strip()
    if transcript_ref:
        record["transcript_ref"] = str(transcript_ref).strip()

    path = _store_path()
    with _write_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            )

    return {
        "created": True,
        "id": record["id"],
        "status": record["status"],
        "business_slug": record["business_slug"],
        "summary": record["summary"],
        "item_count": len(record["items"]),
        "request": record,
    }


def list_open_change_requests(slug: str | None = None) -> dict[str, Any]:
    """List non-terminal ChangeRequests, optionally filtered by business_slug."""
    path = _store_path()
    with _write_lock:
        records = _read_all(path)

    filter_slug = (slug or "").strip() or None
    open_recs: list[dict[str, Any]] = []
    for rec in records:
        status = str(rec.get("status") or "").lower()
        if status not in OPEN_STATUSES:
            continue
        if filter_slug and rec.get("business_slug") != filter_slug:
            continue
        open_recs.append(rec)

    # Newest first for intake agents reading back pending work.
    open_recs.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return {
        "count": len(open_recs),
        "slug": filter_slug,
        "requests": open_recs,
    }


def cancel_change_request(request_id: str) -> dict[str, Any]:
    """Set status=cancelled for an open request. Idempotent if already cancelled."""
    rid = (request_id or "").strip()
    if not rid:
        return {"cancelled": False, "error": "id is required"}

    path = _store_path()
    with _write_lock:
        records = _read_all(path)
        found = None
        for rec in records:
            if rec.get("id") == rid:
                found = rec
                break
        if found is None:
            return {
                "cancelled": False,
                "error": f"no change request with id {rid!r}",
            }
        status = str(found.get("status") or "").lower()
        if status == "cancelled":
            return {
                "cancelled": True,
                "already_cancelled": True,
                "id": rid,
                "status": "cancelled",
            }
        if status in TERMINAL_STATUSES:
            return {
                "cancelled": False,
                "error": f"request {rid} is {status} and cannot be cancelled",
                "status": status,
            }
        found["status"] = "cancelled"
        found["cancelled_at"] = _now_iso()
        _write_all(path, records)

    return {
        "cancelled": True,
        "id": rid,
        "status": "cancelled",
        "business_slug": found.get("business_slug"),
        "summary": found.get("summary"),
    }


# --- Site outline (read-only HTML heuristics) ---------------------------------


class _OutlineParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self._in_title = False
        self._heading_tag: str | None = None
        self._heading_parts: list[str] = []
        self.headings: list[dict[str, str]] = []
        self._skip_depth = 0  # script/style

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in ("script", "style", "noscript"):
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if t == "title":
            self._in_title = True
            return
        if t in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._heading_tag = t
            self._heading_parts = []

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("script", "style", "noscript") and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if t == "title":
            self._in_title = False
            return
        if t in ("h1", "h2", "h3", "h4", "h5", "h6") and self._heading_tag == t:
            text = re.sub(r"\s+", " ", "".join(self._heading_parts)).strip()
            if text:
                self.headings.append({"level": t, "text": text})
            self._heading_tag = None
            self._heading_parts = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        elif self._heading_tag:
            self._heading_parts.append(data)


def _slug_safe(slug: str) -> bool:
    # Only allow simple path segments — no traversal.
    return bool(slug) and ".." not in slug and "/" not in slug and "\\" not in slug


def get_site_outline(slug: str) -> dict[str, Any]:
    """Parse generated-sites/<slug>.html for title and heading outline."""
    s = (slug or "").strip()
    if not s:
        return {"found": False, "error": "slug is required"}
    if s.endswith(".html"):
        s = s[: -len(".html")]
    if not _slug_safe(s):
        return {"found": False, "error": "invalid slug"}

    path = _sites_dir() / f"{s}.html"
    if not path.is_file():
        return {
            "found": False,
            "slug": s,
            "error": f"no site file for {s!r}",
        }

    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {
            "found": False,
            "slug": s,
            "error": f"could not read site file ({e.__class__.__name__})",
        }

    parser = _OutlineParser()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        # Fall back to regex heuristics if the parser chokes on messy HTML.
        title_m = re.search(
            r"<title[^>]*>(.*?)</title>", raw, re.I | re.S
        )
        title = (
            html_lib.unescape(re.sub(r"\s+", " ", title_m.group(1))).strip()
            if title_m
            else ""
        )
        headings = []
        for m in re.finditer(
            r"<h([1-6])[^>]*>(.*?)</h\1>", raw, re.I | re.S
        ):
            text = re.sub(r"<[^>]+>", "", m.group(2))
            text = html_lib.unescape(re.sub(r"\s+", " ", text)).strip()
            if text:
                headings.append({"level": f"h{m.group(1)}", "text": text})
        return {
            "found": True,
            "slug": s,
            "title": title,
            "headings": headings,
            "heading_count": len(headings),
            "path": str(path.name),
        }

    title = re.sub(r"\s+", " ", "".join(parser.title_parts)).strip()
    return {
        "found": True,
        "slug": s,
        "title": title,
        "headings": parser.headings,
        "heading_count": len(parser.headings),
        "path": str(path.name),
    }
