"""File-backed caller profiles keyed by phone number (E.164).

AI 411 MVP store (#49): remembers display/preferred name, preferences,
notes, last topics, and consent flags across calls. Hard-delete via
forget_caller. Not the sales call-log (see calllog.py).

Storage: single JSON object map phone_e164 -> CallerProfile, path from
CALLERS_PATH (default /data/callers.json). Thread-safe with a lock.

Consent gate (get_caller_profile):
  - memory_ok True  → return full profile
  - memory_ok False → return found + phone + consent only (no names,
    preferences, notes, or last_topics). Writes still work so the caller
    can re-enable memory or be forgotten.
"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Monkeypatchable in tests (same pattern as config.CALL_LOG for calllog).
CALLERS_PATH = Path(os.getenv("CALLERS_PATH", "/data/callers.json"))

_lock = threading.Lock()

_TOP_LEVEL_PATCH_KEYS = frozenset({
    "display_name",
    "preferred_name",
    "preferences",
    "notes",
    "last_topics",
    "last_call_at",
    "consent",
})

_PREFERENCE_KEYS = frozenset({
    "interests",
    "avoid",
    "preferred_areas",
    "sms_ok",
    "mobility",
    "accessibility",
})

_CONSENT_KEYS = frozenset({"memory_ok", "marketing_ok"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_phone(phone: str) -> str | None:
    """Normalize to a compact E.164-ish key; empty / garbage → None."""
    if phone is None:
        return None
    raw = str(phone).strip()
    if not raw:
        return None
    # Keep leading + and digits only.
    if raw.startswith("+"):
        digits = re.sub(r"\D", "", raw[1:])
        if len(digits) < 10:
            return None
        return "+" + digits
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 10:
        return None
    # US 10-digit → +1; 11 starting with 1 → +1...
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return "+" + digits


def _default_preferences() -> dict[str, Any]:
    return {
        "interests": [],
        "avoid": [],
        "preferred_areas": [],
        "sms_ok": False,
        "mobility": "",
        "accessibility": "",
    }


def _default_consent() -> dict[str, Any]:
    return {"memory_ok": False, "marketing_ok": False}


def _empty_profile(phone_e164: str) -> dict[str, Any]:
    now = _now_iso()
    return {
        "phone_e164": phone_e164,
        "display_name": "",
        "preferred_name": "",
        "preferences": _default_preferences(),
        "notes": [],
        "last_topics": [],
        "created_at": now,
        "updated_at": now,
        "last_call_at": None,
        "consent": _default_consent(),
    }


def _load_store() -> dict[str, dict]:
    path = CALLERS_PATH
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    # Support either {"profiles": {...}} or a bare phone→profile map.
    if "profiles" in data and isinstance(data["profiles"], dict):
        return data["profiles"]
    return data


def _save_store(profiles: dict[str, dict]) -> None:
    path = CALLERS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = {"profiles": profiles}
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(path)


def _merge_dict(base: dict, patch: dict, allowed: frozenset) -> dict:
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if key not in allowed:
            continue
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            # Shallow merge one level for nested dicts we own.
            nested = copy.deepcopy(out[key])
            nested.update(value)
            out[key] = nested
        else:
            out[key] = copy.deepcopy(value)
    return out


def _apply_patch(profile: dict, patch: dict) -> dict:
    """Deep-merge a caller-supplied patch into profile (mutates a copy)."""
    if not isinstance(patch, dict):
        return profile
    out = copy.deepcopy(profile)

    for key in ("display_name", "preferred_name", "last_call_at"):
        if key in patch and patch[key] is not None:
            out[key] = patch[key]

    if "preferences" in patch and isinstance(patch["preferences"], dict):
        prefs = copy.deepcopy(out.get("preferences") or _default_preferences())
        for pk, pv in patch["preferences"].items():
            if pk not in _PREFERENCE_KEYS:
                continue
            if isinstance(pv, list) and isinstance(prefs.get(pk), list):
                # Replace list when provided (not append) for clear updates.
                prefs[pk] = list(pv)
            else:
                prefs[pk] = pv
        out["preferences"] = prefs

    if "consent" in patch and isinstance(patch["consent"], dict):
        consent = copy.deepcopy(out.get("consent") or _default_consent())
        for ck, cv in patch["consent"].items():
            if ck in _CONSENT_KEYS:
                consent[ck] = bool(cv)
        out["consent"] = consent

    if "notes" in patch and isinstance(patch["notes"], list):
        out["notes"] = list(patch["notes"])

    if "last_topics" in patch and isinstance(patch["last_topics"], list):
        out["last_topics"] = list(patch["last_topics"])

    out["updated_at"] = _now_iso()
    return out


def _public_profile(profile: dict, *, respect_memory: bool = True) -> dict:
    """Return a copy safe to speak; redact memory when memory_ok is false."""
    p = copy.deepcopy(profile)
    consent = p.get("consent") or _default_consent()
    memory_ok = bool(consent.get("memory_ok"))
    if respect_memory and not memory_ok:
        return {
            "found": True,
            "memory_ok": False,
            "phone_e164": p.get("phone_e164"),
            "consent": consent,
            "created_at": p.get("created_at"),
            "updated_at": p.get("updated_at"),
            "last_call_at": p.get("last_call_at"),
            # Explicit empty fields so agents don't invent memory.
            "display_name": "",
            "preferred_name": "",
            "preferences": _default_preferences(),
            "notes": [],
            "last_topics": [],
            "message": (
                "Caller has not consented to memory (consent.memory_ok=false). "
                "Do not personalize from stored prefs; you may ask to enable "
                "memory or proceed without remembering."
            ),
        }
    p["found"] = True
    p["memory_ok"] = memory_ok
    return p


def get_profile(phone: str) -> dict:
    """Load profile by phone. Never raises."""
    key = _normalize_phone(phone)
    if not key:
        return {
            "found": False,
            "error": "invalid or missing phone number — use E.164 or 10-digit US",
        }
    try:
        with _lock:
            profiles = _load_store()
            profile = profiles.get(key)
        if not profile:
            return {"found": False, "phone_e164": key}
        return _public_profile(profile, respect_memory=True)
    except Exception as e:  # noqa: BLE001 — speakable errors only
        return {
            "found": False,
            "error": f"data unavailable ({e.__class__.__name__})",
        }


def update_profile(phone: str, patch: dict | None = None) -> dict:
    """Create or patch a caller profile. Never raises."""
    key = _normalize_phone(phone)
    if not key:
        return {
            "updated": False,
            "error": "invalid or missing phone number — use E.164 or 10-digit US",
        }
    if patch is None:
        patch = {}
    if not isinstance(patch, dict):
        return {
            "updated": False,
            "error": "patch must be an object/dict of fields to change",
        }
    # Reject unknown top-level keys softly (ignore extras, don't fail).
    try:
        with _lock:
            profiles = _load_store()
            existing = profiles.get(key)
            if existing is None:
                profile = _empty_profile(key)
            else:
                profile = copy.deepcopy(existing)
            profile = _apply_patch(profile, patch)
            profile["phone_e164"] = key
            profiles[key] = profile
            _save_store(profiles)
        return {
            "updated": True,
            "phone_e164": key,
            "profile": _public_profile(profile, respect_memory=True),
        }
    except Exception as e:  # noqa: BLE001
        return {
            "updated": False,
            "error": f"data unavailable ({e.__class__.__name__})",
        }


def forget_profile(phone: str) -> dict:
    """Hard-delete caller profile. Never raises."""
    key = _normalize_phone(phone)
    if not key:
        return {
            "forgotten": False,
            "error": "invalid or missing phone number — use E.164 or 10-digit US",
        }
    try:
        with _lock:
            profiles = _load_store()
            if key not in profiles:
                return {
                    "forgotten": True,
                    "phone_e164": key,
                    "existed": False,
                    "message": "no profile on file — nothing to delete",
                }
            del profiles[key]
            _save_store(profiles)
        return {
            "forgotten": True,
            "phone_e164": key,
            "existed": True,
            "message": "caller profile permanently deleted",
        }
    except Exception as e:  # noqa: BLE001
        return {
            "forgotten": False,
            "error": f"data unavailable ({e.__class__.__name__})",
        }


def add_note(phone: str, note: str) -> dict:
    """Append a freeform note to the caller profile (creates if needed)."""
    key = _normalize_phone(phone)
    if not key:
        return {
            "added": False,
            "error": "invalid or missing phone number — use E.164 or 10-digit US",
        }
    text = (note or "").strip()
    if not text:
        return {"added": False, "error": "note must be non-empty"}
    entry = {"text": text, "at": _now_iso()}
    try:
        with _lock:
            profiles = _load_store()
            profile = profiles.get(key)
            if profile is None:
                profile = _empty_profile(key)
            else:
                profile = copy.deepcopy(profile)
            notes = list(profile.get("notes") or [])
            notes.append(entry)
            profile["notes"] = notes
            profile["updated_at"] = _now_iso()
            profiles[key] = profile
            _save_store(profiles)
        return {
            "added": True,
            "phone_e164": key,
            "note": entry,
            "note_count": len(profile["notes"]),
        }
    except Exception as e:  # noqa: BLE001
        return {
            "added": False,
            "error": f"data unavailable ({e.__class__.__name__})",
        }
