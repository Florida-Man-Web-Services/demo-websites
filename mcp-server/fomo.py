"""AI 411 FOMO — opt-in tribe interest matching for events.

Default OFF. Matching and outbound notify require consent.fomo_ok (or
preferences.fomo_calls) AND consent.memory_ok. TCPA-safe: never queue an
outbound *call* without explicit fomo_ok; prefer SMS when preferences.sms_ok;
rate-limit; no spam. Privacy-first speakable text never includes peer phones
or real names — only generic \"someone else into X is interested in Y\".

Storage
  EVENT_INTERESTS_PATH  — JSONL interests (default /data/event_interests.jsonl)
  FOMO_NOTIFY_PATH      — JSONL notify queue (default /data/fomo_notify.jsonl)

Tools
  express_event_interest(phone, event_id)
  list_event_interest_matches(phone, event_id?)
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

_DEFAULT_INTERESTS = Path("/data/event_interests.jsonl")
_FALLBACK_INTERESTS = (
    Path(__file__).resolve().parent.parent / "data" / "event_interests.jsonl"
)
_DEFAULT_NOTIFY = Path("/data/fomo_notify.jsonl")
_FALLBACK_NOTIFY = (
    Path(__file__).resolve().parent.parent / "data" / "fomo_notify.jsonl"
)

EVENT_INTERESTS_PATH = Path(
    os.getenv(
        "EVENT_INTERESTS_PATH",
        str(
            _DEFAULT_INTERESTS
            if _DEFAULT_INTERESTS.parent.exists()
            else _FALLBACK_INTERESTS
        ),
    )
)
FOMO_NOTIFY_PATH = Path(
    os.getenv(
        "FOMO_NOTIFY_PATH",
        str(_DEFAULT_NOTIFY if _DEFAULT_NOTIFY.parent.exists() else _FALLBACK_NOTIFY),
    )
)

_lock = threading.Lock()

# Tunables (env-overridable for tests/ops).
FOMO_MIN_MATCHES = int(os.getenv("FOMO_MIN_MATCHES", "2"))
FOMO_MAX_NOTIFIES_PER_PHONE_PER_DAY = int(
    os.getenv("FOMO_MAX_NOTIFIES_PER_PHONE_PER_DAY", "3")
)
FOMO_NOTIFY_COOLDOWN_HOURS = int(os.getenv("FOMO_NOTIFY_COOLDOWN_HOURS", "24"))


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _now_iso() -> str:
    return _now().isoformat()


def _interests_path() -> Path:
    env = os.getenv("EVENT_INTERESTS_PATH")
    if env:
        return Path(env)
    return Path(EVENT_INTERESTS_PATH)


def _notify_path() -> Path:
    env = os.getenv("FOMO_NOTIFY_PATH")
    if env:
        return Path(env)
    return Path(FOMO_NOTIFY_PATH)


def _normalize_phone(phone: str) -> str | None:
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


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
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
                    rows.append(obj)
    except OSError:
        return []
    return rows


def _rewrite_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(path)


def _load_callers():
    try:
        import callers as callers_mod  # type: ignore

        return callers_mod
    except Exception:  # noqa: BLE001
        return None


def _load_events():
    try:
        import events as events_mod  # type: ignore

        return events_mod
    except Exception:  # noqa: BLE001
        return None


def _consent_flags(phone_e164: str) -> dict[str, Any]:
    """Read memory_ok / fomo_ok / sms_ok without inventing defaults on error."""
    callers = _load_callers()
    out = {
        "found": False,
        "memory_ok": False,
        "fomo_ok": False,
        "sms_ok": False,
        "interests": [],
    }
    if callers is None:
        return out
    try:
        # Bypass memory redaction for gate checks — we only need consent flags.
        with callers._lock:  # noqa: SLF001 — intentional internal read for gates
            profiles = callers._load_store()  # noqa: SLF001
            prof = profiles.get(phone_e164)
        if not prof:
            return out
        consent = prof.get("consent") or {}
        prefs = prof.get("preferences") or {}
        fomo = bool(consent.get("fomo_ok")) or bool(prefs.get("fomo_calls"))
        out.update(
            {
                "found": True,
                "memory_ok": bool(consent.get("memory_ok")),
                "fomo_ok": fomo,
                "sms_ok": bool(prefs.get("sms_ok")),
                "interests": list(prefs.get("interests") or []),
            }
        )
        return out
    except Exception:  # noqa: BLE001
        return out


def _event_summary(event_id: str) -> dict[str, Any]:
    events = _load_events()
    if events is None:
        return {"id": event_id, "title": event_id, "tags": [], "found": False}
    try:
        got = events.get_event(event_id)
        if isinstance(got, dict) and got.get("found"):
            ev = got.get("event") or got
            return {
                "id": str(ev.get("id") or event_id),
                "title": str(ev.get("title") or event_id),
                "tags": list(ev.get("tags") or []),
                "venue": str(ev.get("venue") or ""),
                "start": str(ev.get("start") or ""),
                "found": True,
            }
        # Some get_event shapes return the event at top level with id.
        if isinstance(got, dict) and got.get("id") and got.get("title"):
            return {
                "id": str(got.get("id")),
                "title": str(got.get("title")),
                "tags": list(got.get("tags") or []),
                "venue": str(got.get("venue") or ""),
                "start": str(got.get("start") or ""),
                "found": True,
            }
    except Exception:  # noqa: BLE001
        pass
    return {"id": event_id, "title": event_id, "tags": [], "found": False}


def _interest_topic_label(tags: list[str], event: dict[str, Any]) -> str:
    for t in tags or []:
        tl = str(t).strip().lower()
        if tl and tl not in {"people", "social", "free"}:
            return tl
    for t in event.get("tags") or []:
        tl = str(t).strip().lower()
        if tl and tl not in {"free"}:
            return tl
    return "local events"


def _active_interests_for_event(event_id: str) -> list[dict[str, Any]]:
    eid = str(event_id or "").strip()
    rows = _read_jsonl(_interests_path())
    out: list[dict[str, Any]] = []
    seen_phones: set[str] = set()
    for r in rows:
        if not r.get("active", True):
            continue
        if str(r.get("event_id") or "") != eid:
            continue
        phone = str(r.get("phone_e164") or "")
        if not phone or phone in seen_phones:
            continue
        seen_phones.add(phone)
        out.append(r)
    return out


def _fomo_eligible_peers(event_id: str, *, exclude_phone: str = "") -> list[dict[str, Any]]:
    peers = []
    for row in _active_interests_for_event(event_id):
        phone = str(row.get("phone_e164") or "")
        if not phone or phone == exclude_phone:
            continue
        flags = _consent_flags(phone)
        if flags.get("memory_ok") and flags.get("fomo_ok"):
            peers.append({**row, "_consent": flags})
    return peers


def _parse_iso(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _notifies_today(phone_e164: str) -> int:
    day = _now().date().isoformat()
    n = 0
    for row in _read_jsonl(_notify_path()):
        if str(row.get("to_phone") or "") != phone_e164:
            continue
        at = _parse_iso(str(row.get("at") or ""))
        if at and at.date().isoformat() == day:
            n += 1
    return n


def _recent_notify_for(phone_e164: str, event_id: str) -> bool:
    """True if we already queued a notify for this phone+event within cooldown."""
    cutoff = _now().timestamp() - FOMO_NOTIFY_COOLDOWN_HOURS * 3600
    for row in _read_jsonl(_notify_path()):
        if str(row.get("to_phone") or "") != phone_e164:
            continue
        if str(row.get("event_id") or "") != event_id:
            continue
        at = _parse_iso(str(row.get("at") or ""))
        if at and at.timestamp() >= cutoff:
            return True
    return False


def _sms_template(event: dict[str, Any], topic: str, peer_count: int) -> str:
    title = str(event.get("title") or "a local event")
    others = peer_count - 1 if peer_count > 1 else peer_count
    who = (
        f"someone else into {topic}"
        if others <= 1
        else f"a few others into {topic}"
    )
    venue = str(event.get("venue") or "").strip()
    where = f" at {venue}" if venue else ""
    return (
        f"A411 FOMO: {who} is also interested in {title}{where}. "
        f"Reply STOP to opt out of FOMO alerts."
    )


def _queue_notify_for_phone(
    *,
    phone_e164: str,
    event: dict[str, Any],
    peer_count: int,
    topic: str,
    sms_ok: bool,
) -> dict[str, Any] | None:
    eid = str(event.get("id") or "")
    if not eid:
        return None
    if _recent_notify_for(phone_e164, eid):
        return None
    if _notifies_today(phone_e164) >= FOMO_MAX_NOTIFIES_PER_PHONE_PER_DAY:
        return None

    # Prefer SMS when allowed; always queue call only as stub (no live dialer).
    channel = "sms" if sms_ok else "sms_deferred"
    # Outbound call job stub: recorded when fomo_ok (always true for caller here)
    # but dialer not wired — ops can pick up FOMO_NOTIFY_PATH later.
    call_stub = {
        "id": "fn-" + uuid.uuid4().hex[:12],
        "type": "call_stub",
        "channel": "outbound_call_stub",
        "to_phone": phone_e164,
        "event_id": eid,
        "event_title": event.get("title") or eid,
        "peer_count": peer_count,
        "topic": topic,
        "status": "queued_no_dialer",
        "message": (
            "Outbound FOMO call stub — no dialer wired; do not auto-dial. "
            "Prefer SMS when sms_ok."
        ),
        "at": _now_iso(),
    }
    sms_row = {
        "id": "fn-" + uuid.uuid4().hex[:12],
        "type": "sms",
        "channel": channel,
        "to_phone": phone_e164,
        "event_id": eid,
        "event_title": event.get("title") or eid,
        "peer_count": peer_count,
        "topic": topic,
        "template": _sms_template(event, topic, peer_count),
        "status": "queued" if sms_ok else "held_no_sms_ok",
        "at": _now_iso(),
    }
    _append_jsonl(_notify_path(), sms_row)
    # Only record call stub when SMS not preferred path; still TCPA-safe stub.
    if not sms_ok:
        _append_jsonl(_notify_path(), call_stub)
        return {"sms": sms_row, "call_stub": call_stub}
    return {"sms": sms_row}


def _maybe_queue_fomo_notifies(event_id: str) -> list[dict[str, Any]]:
    """When 2+ fomo_ok+memory_ok callers share event_id, queue notifies."""
    event = _event_summary(event_id)
    peers = []
    for row in _active_interests_for_event(event_id):
        phone = str(row.get("phone_e164") or "")
        flags = _consent_flags(phone)
        if flags.get("memory_ok") and flags.get("fomo_ok"):
            peers.append((phone, flags, row))

    if len(peers) < FOMO_MIN_MATCHES:
        return []

    queued: list[dict[str, Any]] = []
    peer_count = len(peers)
    for phone, flags, row in peers:
        tags = list(row.get("tags") or flags.get("interests") or [])
        topic = _interest_topic_label(tags, event)
        result = _queue_notify_for_phone(
            phone_e164=phone,
            event=event,
            peer_count=peer_count,
            topic=topic,
            sms_ok=bool(flags.get("sms_ok")),
        )
        if result:
            queued.append({"phone": phone, **result})
    return queued


def express_event_interest(
    phone: str,
    event_id: str,
    *,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Record that a caller is interested in attending an event.

    Requires memory_ok. FOMO matching/notify requires fomo_ok as well.
    Never raises.
    """
    try:
        phone_e164 = _normalize_phone(phone)
        if not phone_e164:
            return {
                "ok": False,
                "recorded": False,
                "error": "invalid or missing phone number — use E.164 or 10-digit US",
            }
        eid = str(event_id or "").strip()
        if not eid:
            return {"ok": False, "recorded": False, "error": "event_id is required"}

        flags = _consent_flags(phone_e164)
        if not flags.get("memory_ok"):
            return {
                "ok": False,
                "recorded": False,
                "needs_memory_ok": True,
                "needs_fomo_ok": True,
                "error": (
                    "Caller has not enabled memory (consent.memory_ok). "
                    "Ask to remember them first, then offer FOMO opt-in."
                ),
                "speakable": (
                    "I can only track event interest if you let me remember you. "
                    "Want me to turn memory on, and optionally FOMO alerts when "
                    "someone else into the same things is going?"
                ),
            }

        event = _event_summary(eid)
        if not event.get("found"):
            # Still allow interest on unknown id (seed lag) but flag it.
            event = {"id": eid, "title": eid, "tags": [], "found": False}

        topic_tags: list[str] = []
        for t in list(tags or []) + list(event.get("tags") or []) + list(
            flags.get("interests") or []
        ):
            tl = str(t).strip().lower()
            if tl and tl not in topic_tags:
                topic_tags.append(tl)

        fomo_ok = bool(flags.get("fomo_ok"))
        row = {
            "id": "ei-" + uuid.uuid4().hex[:12],
            "phone_e164": phone_e164,
            "event_id": eid,
            "event_title": event.get("title") or eid,
            "tags": topic_tags[:12],
            "at": _now_iso(),
            "active": True,
            "fomo_ok_at_record": fomo_ok,
        }

        with _lock:
            # Deactivate prior interest rows for same phone+event (keep history).
            path = _interests_path()
            existing = _read_jsonl(path)
            changed = False
            for prev in existing:
                if (
                    prev.get("active", True)
                    and str(prev.get("phone_e164") or "") == phone_e164
                    and str(prev.get("event_id") or "") == eid
                ):
                    prev["active"] = False
                    prev["superseded_at"] = _now_iso()
                    changed = True
            if changed:
                _rewrite_jsonl(path, existing)
            _append_jsonl(path, row)

        queued: list[dict[str, Any]] = []
        match_count = 0
        if fomo_ok:
            with _lock:
                queued = _maybe_queue_fomo_notifies(eid)
            match_count = len(_fomo_eligible_peers(eid, exclude_phone=""))
        else:
            # Count would-be peers only for messaging (no notify).
            match_count = len(_fomo_eligible_peers(eid, exclude_phone=phone_e164))

        topic = _interest_topic_label(topic_tags, event)
        title = event.get("title") or eid

        if not fomo_ok:
            return {
                "ok": True,
                "recorded": True,
                "phone": phone_e164,
                "event_id": eid,
                "event_title": title,
                "fomo_ok": False,
                "needs_fomo_ok": True,
                "peer_matches": match_count,
                "notifies_queued": 0,
                "interest_id": row["id"],
                "speakable": (
                    f"Got it — you're interested in {title}. "
                    f"FOMO alerts are off by default. If you opt in, I can text you "
                    f"when someone else into {topic} is interested in the same event "
                    f"— I never share names or phone numbers. Want FOMO alerts on?"
                ),
                "opt_in_hint": (
                    "Call update_caller_profile with "
                    'consent.fomo_ok=true (and keep memory_ok=true). '
                    "Also set preferences.sms_ok=true to prefer SMS over any call stub."
                ),
            }

        others = max(0, match_count - 1)
        if others >= 1:
            peer_line = (
                f"Someone else into {topic} is also interested in {title}."
                if others == 1
                else f"A few others into {topic} are also interested in {title}."
            )
        else:
            peer_line = (
                f"You're on the list for {title}. I'll let you know if someone "
                f"else into {topic} shows interest — no names or numbers shared."
            )

        return {
            "ok": True,
            "recorded": True,
            "phone": phone_e164,
            "event_id": eid,
            "event_title": title,
            "fomo_ok": True,
            "needs_fomo_ok": False,
            "peer_matches": match_count,
            "notifies_queued": len(queued),
            "notify_jobs": queued,
            "interest_id": row["id"],
            "speakable": peer_line,
            "privacy": (
                "Never speak peer names or phone numbers. "
                "Only generic tribe phrasing."
            ),
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "recorded": False,
            "error": f"fomo interest failed ({e.__class__.__name__})",
        }


def list_event_interest_matches(
    phone: str,
    event_id: str = "",
    *,
    limit: int = 10,
) -> dict[str, Any]:
    """List FOMO matches for this caller's interests (privacy-safe).

    Without event_id: scan all active interests for this phone.
    Requires memory_ok + fomo_ok for match details; otherwise explain opt-in.
    """
    try:
        phone_e164 = _normalize_phone(phone)
        if not phone_e164:
            return {
                "ok": False,
                "matches": [],
                "error": "invalid or missing phone number — use E.164 or 10-digit US",
            }
        lim = max(1, min(int(limit or 10), 25))
        flags = _consent_flags(phone_e164)
        if not flags.get("memory_ok"):
            return {
                "ok": True,
                "matches": [],
                "fomo_ok": False,
                "memory_ok": False,
                "needs_memory_ok": True,
                "speakable": (
                    "I don't have memory enabled for you, so I can't show tribe "
                    "matches. Want to turn memory on first?"
                ),
            }
        if not flags.get("fomo_ok"):
            return {
                "ok": True,
                "matches": [],
                "fomo_ok": False,
                "memory_ok": True,
                "needs_fomo_ok": True,
                "speakable": (
                    "FOMO alerts are off. If you opt in, I can tell you when someone "
                    "else into the same things is interested in the same event — "
                    "never names or phone numbers. Want FOMO on?"
                ),
            }

        with _lock:
            rows = _read_jsonl(_interests_path())

        my_events: list[str] = []
        eid_filter = str(event_id or "").strip()
        for r in rows:
            if not r.get("active", True):
                continue
            if str(r.get("phone_e164") or "") != phone_e164:
                continue
            eid = str(r.get("event_id") or "")
            if not eid:
                continue
            if eid_filter and eid != eid_filter:
                continue
            if eid not in my_events:
                my_events.append(eid)

        matches: list[dict[str, Any]] = []
        for eid in my_events:
            peers = _fomo_eligible_peers(eid, exclude_phone=phone_e164)
            if not peers:
                continue
            event = _event_summary(eid)
            # Gather tags from caller's own interest row
            my_tags: list[str] = []
            for r in rows:
                if (
                    r.get("active", True)
                    and str(r.get("phone_e164") or "") == phone_e164
                    and str(r.get("event_id") or "") == eid
                ):
                    my_tags = list(r.get("tags") or [])
                    break
            topic = _interest_topic_label(
                my_tags or list(flags.get("interests") or []), event
            )
            peer_n = len(peers)
            title = event.get("title") or eid
            speakable = (
                f"Someone else into {topic} is interested in {title}."
                if peer_n == 1
                else f"{peer_n} others into {topic} are interested in {title}."
            )
            matches.append(
                {
                    "event_id": eid,
                    "event_title": title,
                    "peer_count": peer_n,
                    # Deliberately no phones / names
                    "topic": topic,
                    "speakable": speakable,
                    "venue": event.get("venue") or "",
                }
            )
            if len(matches) >= lim:
                break

        if not matches:
            return {
                "ok": True,
                "matches": [],
                "fomo_ok": True,
                "memory_ok": True,
                "count": 0,
                "speakable": (
                    "No tribe matches yet on your event interests. "
                    "When someone else opts in for the same event, I can tip you off."
                ),
            }

        summary_bits = [m["speakable"] for m in matches[:3]]
        return {
            "ok": True,
            "matches": matches,
            "fomo_ok": True,
            "memory_ok": True,
            "count": len(matches),
            "speakable": " ".join(summary_bits),
            "privacy": "No peer names or phone numbers are included.",
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "matches": [],
            "error": f"fomo match list failed ({e.__class__.__name__})",
        }


def clear_interests_for_phone(phone: str) -> dict[str, Any]:
    """Deactivate all interests for a phone (used with forget_caller)."""
    try:
        phone_e164 = _normalize_phone(phone)
        if not phone_e164:
            return {"ok": False, "cleared": 0, "error": "invalid phone"}
        with _lock:
            path = _interests_path()
            rows = _read_jsonl(path)
            n = 0
            for r in rows:
                if str(r.get("phone_e164") or "") == phone_e164 and r.get(
                    "active", True
                ):
                    r["active"] = False
                    r["cleared_at"] = _now_iso()
                    n += 1
            if n:
                _rewrite_jsonl(path, rows)
        return {"ok": True, "cleared": n, "phone": phone_e164}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "cleared": 0, "error": f"clear failed ({e.__class__.__name__})"}


def list_notify_queue(*, limit: int = 20, status: str = "") -> dict[str, Any]:
    """Ops/debug: recent FOMO notify jobs (may include phones — not for voice)."""
    try:
        lim = max(1, min(int(limit or 20), 100))
        rows = list(reversed(_read_jsonl(_notify_path())))
        st = (status or "").strip().lower()
        if st:
            rows = [r for r in rows if str(r.get("status") or "").lower() == st]
        rows = rows[:lim]
        return {"ok": True, "count": len(rows), "jobs": rows}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "jobs": [], "error": f"list failed ({e.__class__.__name__})"}
