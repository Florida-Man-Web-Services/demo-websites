"""Question of the Day store for Gainesville AI 411.

Goals:
  - Rotate a people-oriented QOTD (shared calendar day, America/New_York)
  - Record answers keyed by caller phone (builds a long-horizon people profile)
  - Accept community suggestions for future QOTDs
  - Match local events to the caller's accumulated interests + answer tags

Storage: QOTD_PATH env (default /data/qotd.json; repo data/qotd.json fallback).
Thread-safe. Tool helpers return speakable dicts and never raise to MCP wrappers.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

_DEFAULT_DATA = Path(__file__).resolve().parent.parent / "data" / "qotd.json"
QOTD_PATH = Path(os.getenv("QOTD_PATH", "/data/qotd.json"))

_lock = threading.Lock()

# People-first seed bank — about who you are with / around other people.
_SEED_QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "q-seed-crowd-energy",
        "text": (
            "What kind of crowd makes you feel most like yourself — "
            "small familiar faces, or a lively room of new people?"
        ),
        "category": "people",
        "tags": ["social", "community"],
    },
    {
        "id": "q-seed-ideal-hang",
        "text": (
            "If a friend texted you right now for a spontaneous hang, "
            "what would you hope they suggested?"
        ),
        "category": "people",
        "tags": ["social", "nightlife", "food"],
    },
    {
        "id": "q-seed-stranger-chat",
        "text": (
            "Who is someone you'd love to end up talking to at a local event — "
            "what are they usually into?"
        ),
        "category": "people",
        "tags": ["community", "arts", "music"],
    },
    {
        "id": "q-seed-bring-people",
        "text": (
            "What activity always makes you want to bring someone along "
            "instead of going alone?"
        ),
        "category": "people",
        "tags": ["family", "outdoors", "food"],
    },
    {
        "id": "q-seed-quiet-vs-loud",
        "text": (
            "After a long week, do you recharge better with one close person "
            "or a whole group that keeps the energy up?"
        ),
        "category": "people",
        "tags": ["social"],
    },
    {
        "id": "q-seed-shared-hobby",
        "text": (
            "What hobby or interest would you love to share with people "
            "you haven't met yet in Gainesville?"
        ),
        "category": "people",
        "tags": ["community", "arts", "outdoors", "music"],
    },
    {
        "id": "q-seed-first-hello",
        "text": (
            "At a farmers market, show, or free outdoor night — "
            "what would make you say hello to a stranger?"
        ),
        "category": "people",
        "tags": ["community", "food", "outdoors", "free"],
    },
    {
        "id": "q-seed-values-room",
        "text": (
            "What values do you want the people around you to share — "
            "kindness, curiosity, ambition, creativity, something else?"
        ),
        "category": "people",
        "tags": ["community", "arts"],
    },
    {
        "id": "q-seed-learn-together",
        "text": (
            "If you joined a small group this month, would you rather learn "
            "something new together, make something, or just hang out?"
        ),
        "category": "people",
        "tags": ["community", "arts", "family"],
    },
    {
        "id": "q-seed-night-out-people",
        "text": (
            "Who do you want next to you on a Gainesville night out — "
            "close friends only, a mixed group, or whoever shows up?"
        ),
        "category": "people",
        "tags": ["nightlife", "music", "food", "social"],
    },
]

# Map answer words → event / interest tags for matching.
_TAG_KEYWORDS: dict[str, tuple[str, ...]] = {
    "music": ("music", "concert", "band", "dj", "live music", "sing", "song", "jazz", "rock"),
    "food": ("food", "eat", "dinner", "lunch", "brunch", "coffee", "restaurant", "market", "cook", "farmers"),
    "outdoors": ("outdoor", "outside", "hike", "park", "nature", "trail", "bike", "kayak", "garden"),
    "arts": ("art", "museum", "gallery", "theater", "theatre", "poetry", "craft", "creative", "paint"),
    "nightlife": ("bar", "club", "nightlife", "party", "night out", "drinks", "dance"),
    "family": ("family", "kids", "kid", "children", "parent", "toddler"),
    "sports": ("sport", "game", "fitness", "run", "yoga", "gym", "soccer", "football", "basketball"),
    "community": ("community", "volunteer", "neighborhood", "local", "meetup", "group", "club"),
    "free": ("free", "no cost", "cheap", "budget"),
    "social": ("friends", "people", "crowd", "hang", "together", "introvert", "extrovert", "group"),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _today_et() -> date:
    return datetime.now(ET).date()


def _store_path() -> Path:
    env = os.getenv("QOTD_PATH")
    if env:
        return Path(env)
    path = Path(QOTD_PATH)
    if path == Path("/data/qotd.json") and not path.parent.exists():
        return _DEFAULT_DATA
    return path


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


def _empty_store() -> dict[str, Any]:
    return {
        "questions": copy.deepcopy(_SEED_QUESTIONS),
        "daily": {},  # YYYY-MM-DD -> {question_id, text, category, tags}
        "answers": {},  # phone -> [ {date, question_id, answer, tags, at} ]
        "suggestions": [],  # {id, text, from_phone, at, status}
    }


def _load_store() -> dict[str, Any]:
    path = _store_path()
    if not path.exists():
        return _empty_store()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return _empty_store()
    if not isinstance(data, dict):
        return _empty_store()
    store = _empty_store()
    if isinstance(data.get("questions"), list) and data["questions"]:
        store["questions"] = data["questions"]
    if isinstance(data.get("daily"), dict):
        store["daily"] = data["daily"]
    if isinstance(data.get("answers"), dict):
        store["answers"] = data["answers"]
    if isinstance(data.get("suggestions"), list):
        store["suggestions"] = data["suggestions"]
    return store


def _save_store(store: dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(path)


def _pick_question_for_day(day: date, questions: list[dict[str, Any]]) -> dict[str, Any]:
    active = [q for q in questions if q.get("active", True) and str(q.get("text") or "").strip()]
    if not active:
        active = list(_SEED_QUESTIONS)
    # Stable rotation: hash of ISO date → index
    h = int(hashlib.sha256(day.isoformat().encode()).hexdigest()[:8], 16)
    return active[h % len(active)]


def _ensure_daily(store: dict[str, Any], day: date | None = None) -> dict[str, Any]:
    day = day or _today_et()
    key = day.isoformat()
    daily = store.setdefault("daily", {})
    if key in daily and isinstance(daily[key], dict) and daily[key].get("text"):
        return daily[key]
    q = _pick_question_for_day(day, store.get("questions") or [])
    entry = {
        "date": key,
        "question_id": q.get("id") or f"q-{key}",
        "text": str(q.get("text") or "").strip(),
        "category": str(q.get("category") or "people"),
        "tags": list(q.get("tags") or ["people"]),
    }
    daily[key] = entry
    return entry


def extract_tags_from_text(text: str) -> list[str]:
    """Lightweight keyword → interest tags for event matching."""
    blob = (text or "").lower()
    found: list[str] = []
    for tag, kws in _TAG_KEYWORDS.items():
        if any(kw in blob for kw in kws):
            found.append(tag)
    # always keep people/social signal when they answered a people QOTD
    if "social" not in found and blob:
        found.append("social")
    return found


def get_question_of_the_day(*, day: str = "") -> dict[str, Any]:
    """Return today's people-oriented QOTD (creates daily entry if needed)."""
    try:
        with _lock:
            store = _load_store()
            d: date | None = None
            if day and str(day).strip():
                try:
                    d = date.fromisoformat(str(day).strip()[:10])
                except ValueError:
                    return {"ok": False, "error": "day must be YYYY-MM-DD"}
            entry = _ensure_daily(store, d)
            _save_store(store)
            return {
                "ok": True,
                "date": entry["date"],
                "question_id": entry["question_id"],
                "text": entry["text"],
                "category": entry.get("category") or "people",
                "tags": entry.get("tags") or [],
                "prompt_hint": (
                    "Ask this question conversationally. It is about people and "
                    "how the caller likes to be around others. After they answer, "
                    "invite one short suggestion for a future question of the day."
                ),
            }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"qotd unavailable ({e.__class__.__name__})"}


def answer_question_of_the_day(
    phone: str,
    answer: str,
    *,
    question_id: str = "",
    tags: list[str] | None = None,
    day: str = "",
) -> dict[str, Any]:
    """Record a caller's QOTD answer and fold tags into their people profile signal."""
    try:
        phone_e164 = _normalize_phone(phone)
        if not phone_e164:
            return {"ok": False, "recorded": False, "error": "invalid phone"}
        text = str(answer or "").strip()
        if not text:
            return {"ok": False, "recorded": False, "error": "empty answer"}
        if len(text) > 2000:
            text = text[:2000]

        with _lock:
            store = _load_store()
            d: date | None = None
            if day and str(day).strip():
                try:
                    d = date.fromisoformat(str(day).strip()[:10])
                except ValueError:
                    return {"ok": False, "recorded": False, "error": "day must be YYYY-MM-DD"}
            entry = _ensure_daily(store, d)
            qid = str(question_id or entry["question_id"]).strip()
            auto_tags = extract_tags_from_text(text)
            q_tags = [str(t) for t in (entry.get("tags") or [])]
            extra = [str(t).strip().lower() for t in (tags or []) if str(t).strip()]
            merged_tags: list[str] = []
            for t in q_tags + auto_tags + extra:
                tl = t.lower().strip()
                if tl and tl not in merged_tags:
                    merged_tags.append(tl)

            rec = {
                "date": entry["date"],
                "question_id": qid,
                "question_text": entry.get("text") or "",
                "answer": text,
                "tags": merged_tags,
                "at": _now_iso(),
            }
            answers = store.setdefault("answers", {})
            hist = list(answers.get(phone_e164) or [])
            # Replace same-day answer if re-answered
            hist = [h for h in hist if h.get("date") != entry["date"]]
            hist.append(rec)
            # Cap history
            hist = hist[-60:]
            answers[phone_e164] = hist
            _save_store(store)

        # Best-effort: merge tags into caller profile interests (consent auto via callers)
        profile_patch_result: dict[str, Any] | None = None
        try:
            import callers as callers_mod  # type: ignore

            existing_interests: list[str] = []
            try:
                prev = callers_mod.get_profile(phone_e164)
                if prev.get("found") and prev.get("memory_ok"):
                    existing_interests = list(
                        (prev.get("preferences") or {}).get("interests") or []
                    )
            except Exception:  # noqa: BLE001
                existing_interests = []
            interests: list[str] = []
            for t in existing_interests + merged_tags + ["people"]:
                tl = str(t).lower().strip()
                if tl and tl not in interests:
                    interests.append(tl)
            profile_patch_result = callers_mod.update_profile(
                phone_e164,
                {
                    "consent": {"memory_ok": True},
                    "preferences": {"interests": interests},
                    "last_topics": ["question_of_the_day", "people"],
                },
            )
            try:
                callers_mod.add_note(
                    phone_e164, f"QOTD {entry['date']}: {text[:200]}"
                )
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            profile_patch_result = None

        return {
            "ok": True,
            "recorded": True,
            "phone": phone_e164,
            "date": entry["date"],
            "question_id": qid,
            "tags": merged_tags,
            "profile_updated": bool(
                profile_patch_result and profile_patch_result.get("updated")
            ),
            "speakable": (
                "Got it — I'll remember that about how you like to be around people. "
                "Want to suggest a future question of the day for other callers?"
            ),
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "recorded": False,
            "error": f"qotd answer failed ({e.__class__.__name__})",
        }


def suggest_question_of_the_day(phone: str, suggestion: str) -> dict[str, Any]:
    """Caller suggests a future QOTD (people-oriented preferred)."""
    try:
        phone_e164 = _normalize_phone(phone) or ""
        text = str(suggestion or "").strip()
        if not text:
            return {"ok": False, "accepted": False, "error": "empty suggestion"}
        if len(text) > 500:
            text = text[:500]
        # Light policy: reject obvious spam
        low = text.lower()
        if any(x in low for x in ("http://", "https://", "buy now", "crypto pump")):
            return {
                "ok": False,
                "accepted": False,
                "error": "suggestion looks like spam; ask for a simple people question",
            }

        sid = "sug-" + hashlib.sha256(
            f"{phone_e164}:{text}:{_now_iso()}".encode()
        ).hexdigest()[:12]
        row = {
            "id": sid,
            "text": text,
            "from_phone": phone_e164,
            "at": _now_iso(),
            "status": "pending",
            "category": "people",
        }
        with _lock:
            store = _load_store()
            sug = store.setdefault("suggestions", [])
            sug.append(row)
            # Cap pending pile
            if len(sug) > 500:
                store["suggestions"] = sug[-500:]
            # Promote strong people-ish suggestions into the active bank (moderated lightly)
            peopleish = any(
                w in low
                for w in (
                    "people",
                    "friend",
                    "crowd",
                    "together",
                    "stranger",
                    "hang",
                    "group",
                    "community",
                    "who",
                    "someone",
                )
            )
            promoted = False
            if peopleish and len(text) >= 20:
                qid = "q-community-" + sid[4:]
                store.setdefault("questions", []).append(
                    {
                        "id": qid,
                        "text": text if text.endswith("?") else text.rstrip(".") + "?",
                        "category": "people",
                        "tags": extract_tags_from_text(text) or ["people", "social"],
                        "source": "community",
                        "active": True,
                        "from_suggestion": sid,
                    }
                )
                row["status"] = "accepted"
                promoted = True
            _save_store(store)

        return {
            "ok": True,
            "accepted": True,
            "suggestion_id": sid,
            "promoted_to_bank": promoted,
            "speakable": (
                "Love that — I saved your question idea for the community. "
                "Thanks for helping A411 get better."
                if promoted
                else "Saved your question idea — thank you."
            ),
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "accepted": False,
            "error": f"suggestion failed ({e.__class__.__name__})",
        }


def get_caller_people_profile(phone: str) -> dict[str, Any]:
    """Summarize QOTD answers + tags for a caller (people profile over time)."""
    try:
        phone_e164 = _normalize_phone(phone)
        if not phone_e164:
            return {"ok": False, "found": False, "error": "invalid phone"}
        with _lock:
            store = _load_store()
            hist = list((store.get("answers") or {}).get(phone_e164) or [])
        if not hist:
            return {
                "ok": True,
                "found": False,
                "phone": phone_e164,
                "answer_count": 0,
                "tags": [],
                "recent_answers": [],
            }
        tag_counts: dict[str, int] = {}
        for h in hist:
            for t in h.get("tags") or []:
                tl = str(t).lower().strip()
                if tl:
                    tag_counts[tl] = tag_counts.get(tl, 0) + 1
        ranked = sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        tags = [t for t, _ in ranked]
        recent = [
            {
                "date": h.get("date"),
                "question": (h.get("question_text") or "")[:160],
                "answer": (h.get("answer") or "")[:200],
                "tags": h.get("tags") or [],
            }
            for h in hist[-5:]
        ]
        return {
            "ok": True,
            "found": True,
            "phone": phone_e164,
            "answer_count": len(hist),
            "tags": tags,
            "top_interests": tags[:8],
            "recent_answers": recent,
            "speakable_summary": (
                f"You've answered {len(hist)} question"
                f"{'s' if len(hist) != 1 else ''} of the day. "
                + (
                    "You tend toward: " + ", ".join(tags[:5]) + "."
                    if tags
                    else "Still learning your people vibe."
                )
            ),
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "found": False,
            "error": f"people profile failed ({e.__class__.__name__})",
        }


def match_events_for_profile(
    phone: str,
    *,
    when: str = "",
    limit: int = 5,
    free_only: bool = False,
) -> dict[str, Any]:
    """Find local events that fit the caller's QOTD/people profile + interests."""
    try:
        phone_e164 = _normalize_phone(phone)
        if not phone_e164:
            return {"ok": False, "error": "invalid phone", "events": []}

        people = get_caller_people_profile(phone_e164)
        tags = list(people.get("top_interests") or people.get("tags") or [])

        # Merge caller profile interests if available
        try:
            import callers as callers_mod  # type: ignore

            prof = callers_mod.get_profile(phone_e164)
            if prof.get("found") and prof.get("memory_ok"):
                prefs = prof.get("preferences") or {}
                for t in prefs.get("interests") or []:
                    tl = str(t).lower().strip()
                    if tl and tl not in tags:
                        tags.append(tl)
        except Exception:  # noqa: BLE001
            pass

        # Drop pure meta tags that don't help event search
        search_tags = [
            t
            for t in tags
            if t
            not in {
                "people",
                "social",
                "question_of_the_day",
            }
        ]
        query = " ".join(search_tags[:4])
        lim = max(1, min(int(limit or 5), 10))

        import events as events_mod  # type: ignore

        result = events_mod.search_events(
            query=query,
            when=str(when or ""),
            tags=search_tags[:6] or None,
            free_only=bool(free_only),
            limit=lim,
        )
        # If tag filter emptied results, retry with query only / anything
        events_list = []
        if isinstance(result, dict):
            events_list = result.get("events") or result.get("results") or []
            if not events_list and (query or search_tags):
                result = events_mod.search_events(
                    query=query or "community",
                    when=str(when or ""),
                    free_only=bool(free_only),
                    limit=lim,
                )
                events_list = (
                    (result or {}).get("events")
                    or (result or {}).get("results")
                    or []
                )

        return {
            "ok": True,
            "phone": phone_e164,
            "profile_tags": tags,
            "search_tags": search_tags,
            "query": query,
            "when": when or "",
            "count": len(events_list),
            "events": events_list,
            "people_profile": {
                "answer_count": people.get("answer_count", 0),
                "speakable_summary": people.get("speakable_summary"),
            },
            "speakable": (
                f"I found {len(events_list)} event"
                f"{'s' if len(events_list) != 1 else ''} that fit people like you."
                if events_list
                else "I don't have a strong event match yet — try answering today's "
                "question so I can learn your vibe, or tell me a category you like."
            ),
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"match failed ({e.__class__.__name__})",
            "events": [],
        }


def list_question_suggestions(*, limit: int = 10, status: str = "pending") -> dict[str, Any]:
    """Ops/debug: list community QOTD suggestions."""
    try:
        lim = max(1, min(int(limit or 10), 50))
        st = (status or "pending").strip().lower()
        with _lock:
            store = _load_store()
            rows = list(store.get("suggestions") or [])
        if st and st != "all":
            rows = [r for r in rows if str(r.get("status") or "").lower() == st]
        rows = list(reversed(rows))[:lim]
        return {"ok": True, "count": len(rows), "suggestions": rows}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"list failed ({e.__class__.__name__})"}
