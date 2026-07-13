"""File-backed moderated broadcast store for AI 411 community posts (#50 MVP).

JSONL store under BROADCASTS_PATH (default: /data/broadcasts.jsonl, with a
repo-local data/broadcasts.jsonl fallback when /data is unavailable).
Thread-safe via a module lock. Sync helpers never raise — they return
speakable dicts for the voice agent.

Post types
  - event:  title, when_start (ISO), when_end?, venue, free?, tags, url?, text?
  - notice: text (<=280 chars), category (tips|music|food|traffic|general),
            expires_at?

Statuses: pending | approved | rejected | reported | deleted

Moderation (v1)
  - Auto-approve posts that pass the keyword blocklist (mark status=approved
    immediately). High-risk hits are rejected, not held pending.
  - Rate limit: MAX_POSTS_PER_PHONE_PER_DAY submissions per phone per UTC day.
  - Simple blocklist keywords (BLOCKLIST_KEYWORDS; includes a test word).
  - report_broadcast flags a post (status → reported) with reason + reporter.
  - delete_own_broadcast soft-deletes (status → deleted) when author phone matches.

Author key: phone_e164 (normalized like callers; local helper, no import of
callers internals).
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Monkeypatchable in tests (also settable via BROADCASTS_PATH env).
_DEFAULT_DATA = Path("/data/broadcasts.jsonl")
_FALLBACK_DATA = (
    Path(__file__).resolve().parent.parent / "data" / "broadcasts.jsonl"
)
BROADCASTS_PATH = Path(
    os.getenv(
        "BROADCASTS_PATH",
        str(_DEFAULT_DATA if _DEFAULT_DATA.parent.exists() else _FALLBACK_DATA),
    )
)

_write_lock = threading.Lock()

MAX_POSTS_PER_PHONE_PER_DAY = int(os.getenv("BROADCAST_MAX_PER_DAY", "5"))
NOTICE_MAX_CHARS = 280

NOTICE_CATEGORIES = frozenset(
    {"tips", "music", "food", "traffic", "general"}
)

VALID_STATUSES = frozenset(
    {"pending", "approved", "rejected", "reported", "deleted"}
)

# Safety blocklist: empty-ish + one test word for unit tests.
# Matching is case-insensitive substring over concatenated text fields.
BLOCKLIST_KEYWORDS: list[str] = [
    "blocklisttestword",
]

# Rate-limit day key uses UTC calendar day.
_PHONE_RE = re.compile(r"\D+")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _store_path() -> Path:
    env = os.getenv("BROADCASTS_PATH")
    if env:
        return Path(env)
    return Path(BROADCASTS_PATH)


def _normalize_phone(phone: str) -> str | None:
    """Normalize to a compact E.164-ish key; empty / garbage → None."""
    if phone is None:
        return None
    raw = str(phone).strip()
    if not raw:
        return None
    if raw.startswith("+"):
        digits = re.sub(r"\D", "", raw[1:])
        if len(digits) < 10:
            return None
        return "+" + digits
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 10:
        return None
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return "+" + digits


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    # Accept trailing Z
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _read_all(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
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
            f.write(
                json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
                + "\n"
            )
    tmp.replace(path)


def _append_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(
            json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            + "\n"
        )


def _utc_day_key(iso_ts: str | None = None) -> str:
    if iso_ts:
        dt = _parse_iso(iso_ts)
        if dt is not None:
            return dt.astimezone(timezone.utc).date().isoformat()
    return datetime.now(timezone.utc).date().isoformat()


def _count_posts_today(records: list[dict], phone_e164: str) -> int:
    day = _utc_day_key()
    n = 0
    for rec in records:
        if rec.get("author_phone_e164") != phone_e164:
            continue
        # Count all non-deleted submissions for rate limiting.
        if rec.get("status") == "deleted":
            continue
        created = rec.get("created_at") or ""
        if _utc_day_key(created) == day:
            n += 1
    return n


def _text_for_blocklist(fields: list[str | None]) -> str:
    return " ".join(str(f or "") for f in fields).lower()


def _blocklist_hit(text: str) -> str | None:
    for word in BLOCKLIST_KEYWORDS:
        w = (word or "").strip().lower()
        if w and w in text:
            return word
    return None


def _normalize_tags(tags: Any) -> list[str]:
    if tags is None or tags == "":
        return []
    if isinstance(tags, str):
        # comma or space separated
        parts = re.split(r"[,;]+", tags)
        return [p.strip() for p in parts if p.strip()]
    if isinstance(tags, list):
        out: list[str] = []
        for t in tags:
            s = str(t).strip()
            if s:
                out.append(s)
        return out
    return []


def _new_id(prefix: str = "bc") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _public_broadcast(rec: dict[str, Any]) -> dict[str, Any]:
    """Copy safe for list/read responses (no internal-only fields required)."""
    out = {
        "id": rec.get("id"),
        "type": rec.get("type"),
        "status": rec.get("status"),
        "created_at": rec.get("created_at"),
        "updated_at": rec.get("updated_at"),
        "author_phone_e164": rec.get("author_phone_e164"),
    }
    if rec.get("type") == "event":
        out.update(
            {
                "title": rec.get("title"),
                "when_start": rec.get("when_start"),
                "when_end": rec.get("when_end"),
                "venue": rec.get("venue"),
                "free": rec.get("free"),
                "tags": rec.get("tags") or [],
                "url": rec.get("url") or "",
                "text": rec.get("text") or "",
            }
        )
    else:
        out.update(
            {
                "text": rec.get("text"),
                "category": rec.get("category"),
                "expires_at": rec.get("expires_at"),
            }
        )
    if rec.get("report_count"):
        out["report_count"] = rec.get("report_count")
    return out


def _is_expired(rec: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    expires = _parse_iso(rec.get("expires_at"))
    if expires is not None and expires <= now:
        return True
    # Events without explicit expires: treat when_end or when_start as soft end
    # only for listing if past end — optional; MVP uses expires_at primarily.
    if rec.get("type") == "event":
        end = _parse_iso(rec.get("when_end")) or _parse_iso(rec.get("when_start"))
        # Do not auto-expire events solely by when_start for listing of "recent"
        # — list_recent keeps approved non-expired; events without expires_at stay.
        _ = end  # reserved for future event-index merge
    return False


def submit_event_broadcast(
    title: str,
    when_start: str,
    venue: str,
    phone: str,
    when_end: str = "",
    free: bool = True,
    tags: Any = None,
    url: str = "",
    text: str = "",
) -> dict[str, Any]:
    """Create an event broadcast. Auto-approves if blocklist passes.

    Returns speakable dict: submitted, id, status, message, or error.
    """
    try:
        phone_e164 = _normalize_phone(phone)
        if not phone_e164:
            return {
                "submitted": False,
                "error": (
                    "I need a valid phone number to attribute this post — "
                    "ten-digit US or E.164 works."
                ),
            }
        title_s = (title or "").strip()
        if not title_s:
            return {
                "submitted": False,
                "error": "Event title is required — what should we call it?",
            }
        when_s = (when_start or "").strip()
        if not when_s or _parse_iso(when_s) is None:
            return {
                "submitted": False,
                "error": (
                    "when_start must be a valid ISO datetime, "
                    "for example 2026-07-20T19:00:00-04:00."
                ),
            }
        venue_s = (venue or "").strip()
        if not venue_s:
            return {
                "submitted": False,
                "error": "Venue is required — where is the event?",
            }
        if when_end and _parse_iso(when_end) is None:
            return {
                "submitted": False,
                "error": "when_end must be a valid ISO datetime if provided.",
            }

        tag_list = _normalize_tags(tags)
        hit = _blocklist_hit(
            _text_for_blocklist(
                [title_s, venue_s, text, url, " ".join(tag_list)]
            )
        )
        if hit:
            return {
                "submitted": False,
                "status": "rejected",
                "error": (
                    "That post didn't pass our community guidelines filter. "
                    "Please rephrase without prohibited language and try again."
                ),
            }

        path = _store_path()
        with _write_lock:
            records = _read_all(path)
            used = _count_posts_today(records, phone_e164)
            if used >= MAX_POSTS_PER_PHONE_PER_DAY:
                return {
                    "submitted": False,
                    "error": (
                        f"You've hit today's post limit of "
                        f"{MAX_POSTS_PER_PHONE_PER_DAY}. Try again tomorrow."
                    ),
                    "rate_limited": True,
                }

            now = _now_iso()
            # v1: auto-approve low-risk (blocklist already passed).
            status = "approved"
            rec: dict[str, Any] = {
                "id": _new_id("bc"),
                "type": "event",
                "status": status,
                "author_phone_e164": phone_e164,
                "title": title_s,
                "when_start": when_s,
                "when_end": (when_end or "").strip() or None,
                "venue": venue_s,
                "free": bool(free),
                "tags": tag_list,
                "url": (url or "").strip(),
                "text": (text or "").strip(),
                "created_at": now,
                "updated_at": now,
                "reports": [],
                "report_count": 0,
            }
            _append_record(path, rec)

        return {
            "submitted": True,
            "id": rec["id"],
            "status": status,
            "type": "event",
            "message": (
                f"Got it — your event '{title_s}' at {venue_s} is posted "
                f"and live. Reference id {rec['id']}."
            ),
            "broadcast": _public_broadcast(rec),
        }
    except Exception as e:  # never raise
        return {
            "submitted": False,
            "error": (
                f"Couldn't save the event ({e.__class__.__name__}). "
                "Please try again in a moment."
            ),
        }


def submit_notice_broadcast(
    text: str,
    category: str,
    phone: str,
    expires_at: str = "",
) -> dict[str, Any]:
    """Create a short community notice. Auto-approves if blocklist passes."""
    try:
        phone_e164 = _normalize_phone(phone)
        if not phone_e164:
            return {
                "submitted": False,
                "error": (
                    "I need a valid phone number to attribute this post — "
                    "ten-digit US or E.164 works."
                ),
            }
        text_s = (text or "").strip()
        if not text_s:
            return {
                "submitted": False,
                "error": "Notice text is required — what should we share?",
            }
        if len(text_s) > NOTICE_MAX_CHARS:
            return {
                "submitted": False,
                "error": (
                    f"Notices are limited to {NOTICE_MAX_CHARS} characters "
                    f"(yours is {len(text_s)}). Please shorten it."
                ),
            }
        cat = (category or "general").strip().lower()
        if cat not in NOTICE_CATEGORIES:
            return {
                "submitted": False,
                "error": (
                    f"Category must be one of: "
                    f"{', '.join(sorted(NOTICE_CATEGORIES))}."
                ),
            }
        exp_s = (expires_at or "").strip()
        if exp_s and _parse_iso(exp_s) is None:
            return {
                "submitted": False,
                "error": "expires_at must be a valid ISO datetime if provided.",
            }
        # Default expiry: 14 days for gossip/notices when not specified.
        if not exp_s:
            from datetime import timedelta

            exp_s = (
                datetime.now(timezone.utc) + timedelta(days=14)
            ).replace(microsecond=0).isoformat()

        hit = _blocklist_hit(_text_for_blocklist([text_s, cat]))
        if hit:
            return {
                "submitted": False,
                "status": "rejected",
                "error": (
                    "That post didn't pass our community guidelines filter. "
                    "Please rephrase without prohibited language and try again."
                ),
            }

        path = _store_path()
        with _write_lock:
            records = _read_all(path)
            used = _count_posts_today(records, phone_e164)
            if used >= MAX_POSTS_PER_PHONE_PER_DAY:
                return {
                    "submitted": False,
                    "error": (
                        f"You've hit today's post limit of "
                        f"{MAX_POSTS_PER_PHONE_PER_DAY}. Try again tomorrow."
                    ),
                    "rate_limited": True,
                }

            now = _now_iso()
            status = "approved"
            rec: dict[str, Any] = {
                "id": _new_id("bc"),
                "type": "notice",
                "status": status,
                "author_phone_e164": phone_e164,
                "text": text_s,
                "category": cat,
                "expires_at": exp_s,
                "created_at": now,
                "updated_at": now,
                "reports": [],
                "report_count": 0,
            }
            _append_record(path, rec)

        return {
            "submitted": True,
            "id": rec["id"],
            "status": status,
            "type": "notice",
            "message": (
                f"Posted your {cat} notice. It's live now. "
                f"Reference id {rec['id']}."
            ),
            "broadcast": _public_broadcast(rec),
        }
    except Exception as e:
        return {
            "submitted": False,
            "error": (
                f"Couldn't save the notice ({e.__class__.__name__}). "
                "Please try again in a moment."
            ),
        }


def list_recent_broadcasts(
    category: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    """List approved, non-expired broadcasts, newest first.

    category: empty = all; notice categories filter notices; 'event' filters
    to event posts only.
    """
    try:
        try:
            lim = int(limit)
        except (TypeError, ValueError):
            lim = 20
        lim = max(1, min(lim, 100))

        cat = (category or "").strip().lower()
        path = _store_path()
        with _write_lock:
            records = _read_all(path)

        now = datetime.now(timezone.utc)
        out: list[dict[str, Any]] = []
        for rec in records:
            if rec.get("status") != "approved":
                continue
            if _is_expired(rec, now):
                continue
            rtype = rec.get("type")
            if cat:
                if cat == "event":
                    if rtype != "event":
                        continue
                elif cat in NOTICE_CATEGORIES:
                    if rtype != "notice" or (rec.get("category") or "") != cat:
                        continue
                else:
                    # Unknown filter — treat as no match for notices/events by tag
                    tags = rec.get("tags") or []
                    if cat not in [str(t).lower() for t in tags] and cat != (
                        rec.get("category") or ""
                    ):
                        continue
            out.append(_public_broadcast(rec))

        # Newest first
        out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        out = out[:lim]
        return {
            "ok": True,
            "count": len(out),
            "broadcasts": out,
        }
    except Exception as e:
        return {
            "ok": False,
            "count": 0,
            "broadcasts": [],
            "error": (
                f"Couldn't list broadcasts ({e.__class__.__name__}). "
                "Please try again shortly."
            ),
        }


def report_broadcast(
    broadcast_id: str,
    reason: str,
    reporter_phone: str = "",
) -> dict[str, Any]:
    """Flag a broadcast for review (status → reported). Speakable result."""
    try:
        bid = (broadcast_id or "").strip()
        if not bid:
            return {
                "reported": False,
                "error": "Broadcast id is required to file a report.",
            }
        reason_s = (reason or "").strip()
        if not reason_s:
            return {
                "reported": False,
                "error": "Please give a short reason for the report.",
            }
        reporter = _normalize_phone(reporter_phone) if reporter_phone else ""

        path = _store_path()
        with _write_lock:
            records = _read_all(path)
            found = None
            for rec in records:
                if rec.get("id") == bid:
                    found = rec
                    break
            if found is None:
                return {
                    "reported": False,
                    "error": f"No broadcast found with id {bid}.",
                }
            if found.get("status") == "deleted":
                return {
                    "reported": False,
                    "error": "That post was already removed.",
                }

            report_entry = {
                "reason": reason_s,
                "reporter_phone_e164": reporter or None,
                "reported_at": _now_iso(),
            }
            reports = list(found.get("reports") or [])
            reports.append(report_entry)
            found["reports"] = reports
            found["report_count"] = len(reports)
            found["status"] = "reported"
            found["updated_at"] = _now_iso()
            _write_all(path, records)

        return {
            "reported": True,
            "id": bid,
            "status": "reported",
            "message": (
                "Thanks — we flagged that post for review and pulled it from "
                "the public list."
            ),
        }
    except Exception as e:
        return {
            "reported": False,
            "error": (
                f"Couldn't file the report ({e.__class__.__name__}). "
                "Please try again."
            ),
        }


def delete_own_broadcast(broadcast_id: str, phone: str) -> dict[str, Any]:
    """Soft-delete a broadcast if the caller is the author."""
    try:
        bid = (broadcast_id or "").strip()
        if not bid:
            return {
                "deleted": False,
                "error": "Broadcast id is required.",
            }
        phone_e164 = _normalize_phone(phone)
        if not phone_e164:
            return {
                "deleted": False,
                "error": "A valid author phone is required to delete a post.",
            }

        path = _store_path()
        with _write_lock:
            records = _read_all(path)
            found = None
            for rec in records:
                if rec.get("id") == bid:
                    found = rec
                    break
            if found is None:
                return {
                    "deleted": False,
                    "error": f"No broadcast found with id {bid}.",
                }
            if found.get("author_phone_e164") != phone_e164:
                return {
                    "deleted": False,
                    "error": (
                        "That post doesn't belong to this phone number, "
                        "so I can't delete it."
                    ),
                }
            if found.get("status") == "deleted":
                return {
                    "deleted": True,
                    "id": bid,
                    "already_deleted": True,
                    "message": "That post was already removed.",
                }
            found["status"] = "deleted"
            found["updated_at"] = _now_iso()
            _write_all(path, records)

        return {
            "deleted": True,
            "id": bid,
            "status": "deleted",
            "message": "Okay — I took that post down.",
        }
    except Exception as e:
        return {
            "deleted": False,
            "error": (
                f"Couldn't delete the post ({e.__class__.__name__}). "
                "Please try again."
            ),
        }
