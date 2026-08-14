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
    "fomo_calls",  # alias → consent.fomo_ok (FOMO tribe alerts)
    "personal_page",  # alias → consent.personal_page_ok (free personal site)
    "mobility",
    "accessibility",
})

_CONSENT_KEYS = frozenset(
    {"memory_ok", "marketing_ok", "fomo_ok", "personal_page_ok"}
)


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
        "fomo_calls": False,  # default OFF; mirrors consent.fomo_ok when set
        "personal_page": False,  # default OFF; mirrors consent.personal_page_ok
        "mobility": "",
        "accessibility": "",
    }


def _default_consent() -> dict[str, Any]:
    # fomo_ok / personal_page_ok default OFF — explicit opt-in only.
    return {
        "memory_ok": False,
        "marketing_ok": False,
        "fomo_ok": False,
        "personal_page_ok": False,
    }


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


def _coerce_consent_update(patch: dict) -> dict[str, bool] | None:
    """Normalize consent from model/tool patches.

    Models often send ``consent: true`` instead of ``consent: {memory_ok: true}``.
    Also accept top-level ``memory_ok`` / ``marketing_ok`` / ``fomo_ok``.
    ``preferences.fomo_calls`` is an alias for ``consent.fomo_ok``.
    Returns a partial consent dict, or None if the patch did not mention consent.
    """
    if not isinstance(patch, dict):
        return None
    updates: dict[str, bool] = {}
    if "memory_ok" in patch:
        updates["memory_ok"] = bool(patch["memory_ok"])
    if "marketing_ok" in patch:
        updates["marketing_ok"] = bool(patch["marketing_ok"])
    if "fomo_ok" in patch:
        updates["fomo_ok"] = bool(patch["fomo_ok"])
    if "personal_page_ok" in patch:
        updates["personal_page_ok"] = bool(patch["personal_page_ok"])
    prefs = patch.get("preferences")
    if isinstance(prefs, dict) and "fomo_calls" in prefs:
        updates["fomo_ok"] = bool(prefs.get("fomo_calls"))
    if isinstance(prefs, dict) and "personal_page" in prefs:
        updates["personal_page_ok"] = bool(prefs.get("personal_page"))
    if "consent" in patch:
        c = patch["consent"]
        if isinstance(c, bool):
            # Bare true/false ⇒ memory consent (common LLM mistake).
            # Does NOT imply fomo_ok or personal_page_ok (separate opt-ins).
            updates["memory_ok"] = c
        elif isinstance(c, dict):
            for ck, cv in c.items():
                if ck in _CONSENT_KEYS:
                    updates[ck] = bool(cv)
        elif c is None:
            pass
        else:
            # "yes" / "true" strings
            s = str(c).strip().lower()
            if s in ("1", "true", "yes", "y", "on"):
                updates["memory_ok"] = True
            elif s in ("0", "false", "no", "n", "off"):
                updates["memory_ok"] = False
    return updates or None


def _patch_requests_personalization(patch: dict) -> bool:
    """True if the patch stores remember-me content (prefs, name, notes, topics)."""
    if not isinstance(patch, dict):
        return False
    for key in ("display_name", "preferred_name"):
        if key in patch and str(patch.get(key) or "").strip():
            return True
    prefs = patch.get("preferences")
    if isinstance(prefs, dict):
        for pk, pv in prefs.items():
            if pk not in _PREFERENCE_KEYS:
                continue
            if isinstance(pv, list) and any(str(x).strip() for x in pv):
                return True
            if isinstance(pv, str) and pv.strip():
                return True
            if isinstance(pv, bool) and pv:
                return True
    if isinstance(patch.get("notes"), list) and any(
        str((n.get("text") if isinstance(n, dict) else n) or "").strip()
        for n in patch["notes"]
    ):
        return True
    if isinstance(patch.get("last_topics"), list) and any(
        str(t).strip() for t in patch["last_topics"]
    ):
        return True
    return False


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

    consent = copy.deepcopy(out.get("consent") or _default_consent())
    consent_updates = _coerce_consent_update(patch)
    explicit_memory_false = (
        consent_updates is not None and consent_updates.get("memory_ok") is False
    )
    if consent_updates:
        consent.update(consent_updates)
    # "Remember that I like X" without a proper consent object still means
    # store-and-use — unless they explicitly set memory_ok false.
    # Does NOT auto-enable fomo_ok (separate opt-in).
    if _patch_requests_personalization(patch) and not explicit_memory_false:
        consent["memory_ok"] = True
    out["consent"] = consent
    # Keep preference aliases in sync with consent flags when either moves.
    prefs_out = copy.deepcopy(out.get("preferences") or _default_preferences())
    for dk, dv in _default_preferences().items():
        prefs_out.setdefault(dk, dv)
    if consent_updates and "fomo_ok" in consent_updates:
        prefs_out["fomo_calls"] = bool(consent.get("fomo_ok"))
    elif isinstance(patch.get("preferences"), dict) and "fomo_calls" in patch["preferences"]:
        prefs_out["fomo_calls"] = bool(patch["preferences"].get("fomo_calls"))
        consent["fomo_ok"] = bool(prefs_out["fomo_calls"])
        out["consent"] = consent
    if consent_updates and "personal_page_ok" in consent_updates:
        prefs_out["personal_page"] = bool(consent.get("personal_page_ok"))
    elif (
        isinstance(patch.get("preferences"), dict)
        and "personal_page" in patch["preferences"]
    ):
        prefs_out["personal_page"] = bool(patch["preferences"].get("personal_page"))
        consent["personal_page_ok"] = bool(prefs_out["personal_page"])
        out["consent"] = consent
    out["preferences"] = prefs_out

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
        # Best-effort: clear FOMO interests + personal page for this phone.
        try:
            import fomo as fomo_mod  # type: ignore

            fomo_mod.clear_interests_for_phone(key)
        except Exception:  # noqa: BLE001
            pass
        try:
            import personal_pages as pp_mod  # type: ignore

            pp_mod.clear_for_phone(key)
        except Exception:  # noqa: BLE001
            pass
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
    """Append a freeform note to the caller profile (creates if needed).

    Adding a note is an explicit remember-me act → enables memory_ok.
    """
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
            consent = copy.deepcopy(profile.get("consent") or _default_consent())
            consent["memory_ok"] = True
            profile["consent"] = consent
            profile["updated_at"] = _now_iso()
            profiles[key] = profile
            _save_store(profiles)
        return {
            "added": True,
            "phone_e164": key,
            "note": entry,
            "note_count": len(profile["notes"]),
            "memory_ok": True,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "added": False,
            "error": f"data unavailable ({e.__class__.__name__})",
        }
