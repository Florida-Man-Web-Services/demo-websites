"""Owner call auth levels (Phase 1) — F1 now, F2 stub until enroll/verify ships.

See docs/superpowers/specs/2026-08-14-owner-voice-auth-design.md
and skill references/owner-voice-auth.md.

Levels (rank):
  locked (-1) | anonymous (0) | cid_legacy (1) | cid_only (2)
  | voice_soft (3) | voice_hard (4)

step_up_ok is a separate per-call flag for high-risk actions (Phase 3+).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

log = logging.getLogger("voice-agent.voice_auth")

LEVEL_RANK = {
    "locked": -1,
    "anonymous": 0,
    "cid_legacy": 1,
    "cid_only": 2,
    "voice_soft": 3,
    "voice_hard": 4,
}

# Nominal minimum level per owner tool (F2-aware design).
# When VOICE_AUTH_VENDOR=none, write requirements degrade to cid_only
# unless VOICE_ENROLL_REQUIRED_FOR_WRITE is true.
TOOL_MIN_LEVEL: dict[str, str] = {
    "lookup_business": "cid_only",
    "get_site_outline": "cid_only",
    "list_open_change_requests": "cid_only",
    "get_change_request": "cid_only",
    "create_change_request": "voice_soft",
    "cancel_change_request": "voice_soft",
    "apply_change_request": "voice_hard",
    "enroll_voice_auth": "cid_legacy",  # need a phone; handled specially
    "send_sms_links": "cid_only",
    "log_call_outcome": "cid_only",
    # end_call is local and never gated here
}

# High-risk: need step_up_ok even if voice_hard (Phase 3+).
STEP_UP_TOOLS: frozenset[str] = frozenset(
    {
        "apply_change_request",
    }
)

# When anomaly flags fire, these also need step-up (Phase 4).
ANOMALY_STEP_UP_TOOLS: frozenset[str] = frozenset(
    {
        "create_change_request",
        "cancel_change_request",
        "apply_change_request",
    }
)


def voice_auth_vendor() -> str:
    return (os.getenv("VOICE_AUTH_VENDOR") or "none").strip().lower()


def enroll_required_for_write() -> bool:
    return (os.getenv("VOICE_ENROLL_REQUIRED_FOR_WRITE") or "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def effective_min_level(tool_name: str) -> str:
    """Resolve tool minimum, degrading F2 requirements when vendor is stubbed."""
    nominal = TOOL_MIN_LEVEL.get(tool_name, "voice_soft")
    if nominal == "cid_legacy":
        return "cid_legacy"
    vendor = voice_auth_vendor()
    if vendor in ("", "none", "off", "stub") and not enroll_required_for_write():
        if nominal in ("voice_soft", "voice_hard"):
            # Phase 1 default: F1 is enough until speaker-verify ships.
            return "cid_only"
    if enroll_required_for_write() and nominal in ("voice_soft", "voice_hard"):
        return nominal
    return nominal


def level_at_least(have: str | None, need: str | None) -> bool:
    h_name = (have or "anonymous").strip().lower()
    n_name = (need or "anonymous").strip().lower()
    h = LEVEL_RANK.get(h_name, 0)
    n = LEVEL_RANK.get(n_name, 0)
    if h < 0:  # locked
        return False
    # Soft/legacy F1 (Twilio From present, no registry claim) satisfies cid_only tools.
    if n_name == "cid_only" and h_name == "cid_legacy":
        return True
    # cid_only/cid_legacy both OK for enroll (cid_legacy floor)
    if n_name == "cid_legacy" and h_name in ("cid_legacy", "cid_only", "voice_soft", "voice_hard"):
        return True
    return h >= n


def _customers_mod():
    try:
        import sys
        from pathlib import Path

        mcp = Path(__file__).resolve().parent.parent / "mcp-server"
        if str(mcp) not in sys.path:
            sys.path.insert(0, str(mcp))
        import customers

        return customers
    except Exception as e:  # noqa: BLE001
        log.debug("customers import failed for voice_auth: %s", e)
        return None


def dormancy_days() -> int:
    try:
        return max(1, int(os.getenv("VOICE_AUTH_DORMANCY_DAYS", "90") or "90"))
    except ValueError:
        return 90


def template_max_age_days() -> int:
    try:
        return max(1, int(os.getenv("VOICE_AUTH_TEMPLATE_MAX_AGE_DAYS", "365") or "365"))
    except ValueError:
        return 365


def fail_streak_step_up_threshold() -> int:
    try:
        return max(1, int(os.getenv("VOICE_AUTH_FAIL_STREAK_STEP_UP", "3") or "3"))
    except ValueError:
        return 3


def fail_streak_lock_threshold() -> int:
    try:
        return max(1, int(os.getenv("VOICE_AUTH_FAIL_STREAK_LOCK", "8") or "8"))
    except ValueError:
        return 8


def _parse_iso(ts: str | None):
    from datetime import datetime, timezone

    if not ts:
        return None
    s = str(ts).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def analyze_anomaly_flags(
    customer: dict | None,
    caller_number: str,
) -> dict[str, Any]:
    """Phase 4: dormancy, template age, fail streak, new ANI vs last_ani."""
    from datetime import datetime, timezone

    flags: list[str] = []
    reasons: dict[str, Any] = {}
    va = (customer or {}).get("voice_auth") if isinstance(customer, dict) else None
    if not isinstance(va, dict):
        va = {}

    now = datetime.now(timezone.utc)
    last_call = _parse_iso(va.get("last_call_at"))
    if last_call is not None:
        days = (now - last_call).total_seconds() / 86400.0
        reasons["days_since_call"] = round(days, 2)
        if days >= dormancy_days():
            flags.append("dormant")

    enrolled_at = _parse_iso(va.get("enrolled_at"))
    if enrolled_at is not None:
        age = (now - enrolled_at).total_seconds() / 86400.0
        reasons["template_age_days"] = round(age, 2)
        if age >= template_max_age_days():
            flags.append("template_aged")

    streak = int(va.get("fail_streak") or 0)
    reasons["fail_streak"] = streak
    if streak >= fail_streak_lock_threshold():
        flags.append("fail_locked")
    elif streak >= fail_streak_step_up_threshold():
        flags.append("fail_streak")

    # New ANI: caller differs from last_ani on file (e.g. switched trusted phone).
    cust_mod = _customers_mod()
    last_ani = va.get("last_ani") or ""
    cur = caller_number
    if cust_mod:
        cur = cust_mod.normalize_phone(caller_number) or caller_number
        last_ani = cust_mod.normalize_phone(last_ani) or last_ani
    if last_ani and cur and last_ani != cur:
        flags.append("new_ani")
        reasons["last_ani"] = last_ani
        reasons["caller"] = cur

    return {
        "flags": flags,
        "reasons": reasons,
        "require_step_up": bool(
            set(flags) & {"dormant", "template_aged", "fail_streak", "new_ani"}
        ),
        "locked": "fail_locked" in flags,
    }


def compute_initial_auth(
    caller_number: str,
    customer: dict | None = None,
) -> dict[str, Any]:
    """Derive F1 auth snapshot for a new call. Never raises."""
    phone = (caller_number or "").strip()
    cust_mod = _customers_mod()
    enrolled = False
    level = "anonymous"
    matched: list[dict] = []
    primary_customer: dict | None = None
    anomaly: dict[str, Any] = {
        "flags": [],
        "reasons": {},
        "require_step_up": False,
        "locked": False,
    }

    if not phone:
        return {
            "auth_level": "anonymous",
            "voice_enrolled": False,
            "voice_score_ema": None,
            "voice_windows": 0,
            "step_up_ok": False,
            "matched_customers": [],
            "anomaly": anomaly,
            "require_step_up": False,
        }

    if cust_mod is not None:
        try:
            key = cust_mod.normalize_phone(phone)
            phone = key or phone
            matched = cust_mod.find_customers_for_phone(phone)
            if customer and not matched and customer.get("phone"):
                matched = [customer]
            paid = [
                c
                for c in matched
                if cust_mod.is_owner_write_status(c.get("status"))
            ]
            if paid:
                level = "cid_only"
                primary_customer = paid[0]
                for c in paid:
                    va = c.get("voice_auth") or {}
                    if isinstance(va, dict) and (
                        va.get("enrolled_at") or va.get("template_id")
                    ):
                        enrolled = True
                        primary_customer = c
                        break
            elif matched:
                level = "anonymous"
                primary_customer = matched[0]
            else:
                level = "cid_legacy"
        except Exception as e:  # noqa: BLE001
            log.warning("compute_initial_auth customers path failed: %s", e)
            level = "cid_legacy"
    else:
        level = "cid_legacy"

    if primary_customer is None and customer:
        primary_customer = customer
    if primary_customer is not None:
        anomaly = analyze_anomaly_flags(primary_customer, phone)
        if anomaly.get("locked"):
            level = "locked"

    return {
        "auth_level": level,
        "voice_enrolled": enrolled,
        "voice_score_ema": None,
        "voice_windows": 0,
        "step_up_ok": False,
        "matched_customers": matched,
        "anomaly": anomaly,
        "require_step_up": bool(anomaly.get("require_step_up")),
        "primary_customer": primary_customer,
    }


def apply_auth_to_state(state: Any, snapshot: dict[str, Any] | None = None) -> None:
    """Write auth fields onto CallState (duck-typed)."""
    snap = snapshot or compute_initial_auth(
        getattr(state, "caller_number", "") or "",
        getattr(state, "customer", None) or None,
    )
    state.auth_level = snap.get("auth_level") or "anonymous"
    state.voice_enrolled = bool(snap.get("voice_enrolled"))
    state.voice_score_ema = snap.get("voice_score_ema")
    state.voice_windows = int(snap.get("voice_windows") or 0)
    # Anomaly step-up must be earned this call (do not inherit step_up_ok).
    state.step_up_ok = bool(snap.get("step_up_ok"))
    state.auth_anomaly_flags = list((snap.get("anomaly") or {}).get("flags") or [])
    state.auth_require_step_up = bool(snap.get("require_step_up"))
    state.auth_anomaly_reasons = dict((snap.get("anomaly") or {}).get("reasons") or {})
    if snap.get("primary_customer") and not getattr(state, "customer", None):
        state.customer = snap["primary_customer"]
    # Touch last_call only after a real owner session starts (server may call touch).
    if not hasattr(state, "voice_pcm_hashes") or state.voice_pcm_hashes is None:
        state.voice_pcm_hashes = []


def refresh_auth(state: Any) -> str:
    """Recompute F1 level (e.g. after customer status changes mid-call)."""
    snap = compute_initial_auth(
        getattr(state, "caller_number", "") or "",
        getattr(state, "customer", None) or None,
    )
    prev = (getattr(state, "auth_level", None) or "anonymous").strip().lower()
    new = snap["auth_level"]
    # Preserve F2 progress unless anomaly locked.
    if snap.get("auth_level") != "locked" and LEVEL_RANK.get(prev, 0) > LEVEL_RANK.get(
        new, 0
    ) and prev not in ("locked", "anonymous"):
        snap["auth_level"] = prev
        snap["voice_score_ema"] = getattr(state, "voice_score_ema", None)
        snap["voice_windows"] = getattr(state, "voice_windows", 0)
        snap["voice_enrolled"] = getattr(state, "voice_enrolled", False) or snap[
            "voice_enrolled"
        ]
    # Keep step_up_ok if already verified this call
    if getattr(state, "step_up_ok", False):
        snap["step_up_ok"] = True
    apply_auth_to_state(state, snap)
    # Re-apply step_up after apply wiped it when we set True above
    if snap.get("step_up_ok"):
        state.step_up_ok = True
    return state.auth_level


def enroll_owner_on_state(
    state: Any,
    *,
    consent_version: str = "2026-08-14",
    vendor: str | None = None,
    pcm: bytes | None = None,
    sample_rate: int = 8000,
) -> dict[str, Any]:
    """Consent + enroll current caller; update CallState.voice_enrolled."""
    phone = (getattr(state, "caller_number", None) or "").strip()
    cust_mod = _customers_mod()
    if cust_mod is None:
        return {"ok": False, "error": "customers registry unavailable"}
    if not phone:
        return {"ok": False, "error": "caller phone required"}
    v = (vendor if vendor is not None else voice_auth_vendor()) or "none"

    template_id = ""
    quality = 1.0 if v in ("mock", "local_stub", "none") else None
    try:
        from voice_auth_vendors import get_vendor, reset_vendor_cache

        reset_vendor_cache()
        adapter = get_vendor(v)
        if v not in ("", "none", "off", "stub"):
            er = adapter.enroll(
                phone=phone,
                pcm=pcm,
                sample_rate=sample_rate,
                meta={"consent_version": consent_version},
            )
            if er.ok and er.template_id:
                template_id = er.template_id
                quality = er.quality
            elif v == "http" and not er.ok:
                return {
                    "ok": False,
                    "error": er.error or "vendor enroll failed",
                    "code": "enroll_vendor_failed",
                    "vendor": v,
                }
    except Exception as e:  # noqa: BLE001
        log.warning("vendor enroll path failed: %s", e)

    out = cust_mod.mark_voice_enrolled(
        phone,
        vendor=v,
        template_id=template_id,
        consent_version=consent_version,
        quality=quality,
    )
    if out.get("ok"):
        state.voice_enrolled = True
        if out.get("customer"):
            state.customer = out["customer"]
        # Fresh enroll clears template_aged / fail streak anomalies for step-up
        refresh_auth(state)
        state.voice_enrolled = True
        # New enrollment: clear anomaly step-up for template_aged
        flags = list(getattr(state, "auth_anomaly_flags", []) or [])
        flags = [f for f in flags if f != "template_aged"]
        state.auth_anomaly_flags = flags
        state.auth_require_step_up = bool(
            set(flags) & {"dormant", "fail_streak", "new_ani"}
        )
    return out


def verify_enrolled_template(state: Any) -> dict[str, Any]:
    """Return template_id if this caller is enrolled for F2."""
    cust = getattr(state, "customer", None) or {}
    va = cust.get("voice_auth") if isinstance(cust, dict) else None
    if not isinstance(va, dict) or not (va.get("enrolled_at") or va.get("template_id")):
        cust_mod = _customers_mod()
        phone = getattr(state, "caller_number", "") or ""
        if cust_mod and phone:
            row = cust_mod.get(phone)
            if row:
                state.customer = row
                va = row.get("voice_auth") or {}
    if not isinstance(va, dict):
        return {"ok": False, "enrolled": False}
    tid = (va.get("template_id") or "").strip()
    enrolled = bool(va.get("enrolled_at") or tid)
    return {
        "ok": True,
        "enrolled": enrolled,
        "template_id": tid,
        "vendor": va.get("vendor") or "none",
        "fail_streak": int(va.get("fail_streak") or 0),
        "enrolled_at": va.get("enrolled_at"),
    }


def on_speech_window(
    state: Any,
    *,
    pcm: bytes | None = None,
    sample_rate: int = 8000,
) -> dict[str, Any]:
    """Score a speech window and maybe promote auth_level (Phase 2–4)."""
    vendor = voice_auth_vendor()
    if vendor in ("", "none", "off", "stub"):
        return {
            "ok": True,
            "skipped": True,
            "reason": "vendor_none",
            "auth_level": getattr(state, "auth_level", "anonymous"),
        }

    if getattr(state, "auth_level", "") == "locked":
        return {
            "ok": False,
            "error": "auth_locked",
            "auth_level": "locked",
        }

    info = verify_enrolled_template(state)
    if not info.get("enrolled"):
        return {
            "ok": True,
            "skipped": True,
            "reason": "not_enrolled",
            "auth_level": getattr(state, "auth_level", "anonymous"),
        }

    # Anti-replay: reject duplicate PCM fingerprints within the call.
    try:
        from voice_auth_vendors import get_vendor, pcm_fingerprint, reset_vendor_cache
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"vendor import failed: {e}"}

    fp = pcm_fingerprint(pcm)
    hashes = list(getattr(state, "voice_pcm_hashes", None) or [])
    if fp and fp in hashes:
        _record_fail(state, score=None, reason="replay")
        return {
            "ok": False,
            "error": "replay_detected",
            "code": "replay",
            "auth_level": getattr(state, "auth_level", "anonymous"),
        }
    if fp:
        hashes.append(fp)
        # keep last 32
        state.voice_pcm_hashes = hashes[-32:]

    reset_vendor_cache()
    adapter = get_vendor(vendor)
    result = adapter.verify(
        template_id=str(info.get("template_id") or ""),
        pcm=pcm,
        sample_rate=sample_rate,
        meta={
            "call_sid": getattr(state, "call_sid", ""),
            "phone": getattr(state, "caller_number", ""),
            "window": int(getattr(state, "voice_windows", 0) or 0) + 1,
        },
    )

    windows = int(getattr(state, "voice_windows", 0) or 0) + 1
    state.voice_windows = windows
    state.voice_enrolled = True

    if not result.ok or result.score is None:
        _record_fail(state, score=None, reason=result.error or "verify_failed")
        return {
            "ok": False,
            "error": result.error or "verify_failed",
            "auth_level": getattr(state, "auth_level", "anonymous"),
            "windows": windows,
            "vendor": vendor,
        }

    if not result.liveness_ok:
        _record_fail(state, score=float(result.score), reason="liveness_failed")
        return {
            "ok": False,
            "error": "liveness_failed",
            "code": "liveness",
            "score": float(result.score),
            "auth_level": getattr(state, "auth_level", "anonymous"),
            "windows": windows,
            "vendor": vendor,
        }

    score = float(result.score)
    soft = float(os.getenv("VOICE_AUTH_SOFT", "0.75"))
    hard = float(os.getenv("VOICE_AUTH_HARD", "0.85"))
    min_w = int(os.getenv("VOICE_AUTH_MIN_WINDOWS", "3"))

    if score < soft:
        _record_fail(state, score=score, reason="low_score")
        # demote soft progress on bad windows
        if getattr(state, "auth_level", "") in ("voice_soft", "voice_hard"):
            # keep level but streak rises
            pass
        return {
            "ok": True,
            "score": score,
            "auth_level": getattr(state, "auth_level", "anonymous"),
            "windows": windows,
            "promoted": False,
            "vendor": vendor,
            "low_score": True,
        }

    ema = getattr(state, "voice_score_ema", None)
    state.voice_score_ema = score if ema is None else (0.6 * float(ema) + 0.4 * score)
    _record_success(state, score=score)

    promoted = False
    if windows >= min_w and float(state.voice_score_ema) >= hard:
        if getattr(state, "auth_level", "") != "voice_hard":
            promoted = True
        state.auth_level = "voice_hard"
    elif windows >= min_w and float(state.voice_score_ema) >= soft:
        if LEVEL_RANK.get(getattr(state, "auth_level", ""), 0) < LEVEL_RANK["voice_soft"]:
            state.auth_level = "voice_soft"
            promoted = True

    return {
        "ok": True,
        "score": score,
        "auth_level": state.auth_level,
        "windows": windows,
        "template_id": info.get("template_id"),
        "vendor": vendor,
        "promoted": promoted,
        "liveness_ok": True,
    }


def _record_fail(state: Any, *, score: float | None, reason: str) -> None:
    phone = getattr(state, "caller_number", "") or ""
    cust_mod = _customers_mod()
    if cust_mod and phone:
        try:
            out = cust_mod.record_voice_verify_result(phone, ok=False, score=score)
            if out.get("customer"):
                state.customer = out["customer"]
                va = out["customer"].get("voice_auth") or {}
                streak = int(va.get("fail_streak") or 0)
                if streak >= fail_streak_lock_threshold():
                    state.auth_level = "locked"
                elif streak >= fail_streak_step_up_threshold():
                    state.auth_require_step_up = True
                    flags = list(getattr(state, "auth_anomaly_flags", []) or [])
                    if "fail_streak" not in flags:
                        flags.append("fail_streak")
                    state.auth_anomaly_flags = flags
        except Exception as e:  # noqa: BLE001
            log.debug("record fail streak failed: %s", e)
    log.info(
        "voice_auth fail reason=%s score=%s phone=%s",
        reason,
        score,
        phone[-4:] if phone else "",
    )


def _record_success(state: Any, *, score: float) -> None:
    phone = getattr(state, "caller_number", "") or ""
    cust_mod = _customers_mod()
    if cust_mod and phone:
        try:
            out = cust_mod.record_voice_verify_result(phone, ok=True, score=score)
            if out.get("customer"):
                state.customer = out["customer"]
        except Exception as e:  # noqa: BLE001
            log.debug("record success failed: %s", e)


def touch_call_start(state: Any) -> None:
    """Record last_call_at / last_ani after anomaly snapshot is computed."""
    mode = (getattr(state, "mode", None) or "").strip().lower()
    if mode not in ("owner_updates", "unified"):
        return
    phone = getattr(state, "caller_number", "") or ""
    cust_mod = _customers_mod()
    if not cust_mod or not phone:
        return
    try:
        cust_mod.touch_owner_call(phone, ani=phone)
    except Exception as e:  # noqa: BLE001
        log.debug("touch_owner_call failed: %s", e)


def check_tool_allowed(state: Any, tool_name: str) -> dict[str, Any] | None:
    """Return speakable deny dict if tool blocked; None if allowed."""
    name = (tool_name or "").strip()
    if not name or name in (
        "end_call",
        "request_step_up_code",
        "verify_step_up_code",
    ):
        return None

    need = effective_min_level(name)
    have = (getattr(state, "auth_level", None) or "anonymous").strip().lower()

    if have == "locked":
        return {
            "ok": False,
            "denied": True,
            "error": (
                "This line is locked for site updates right now. "
                "A human on our team will need to help."
            ),
            "code": "auth_locked",
            "auth_level": have,
            "required_level": need,
        }

    if not level_at_least(have, need):
        if have == "anonymous":
            msg = (
                "I can only update a site from the owner's registered phone line "
                "after the account is active. I can take a note for the team instead."
            )
            code = "auth_anonymous"
        elif need in ("voice_soft", "voice_hard"):
            msg = (
                "I need a bit more of your natural speech to confirm it's you — "
                "keep talking about the change you want."
            )
            code = "auth_voice_pending"
        else:
            msg = (
                "I couldn't verify this phone for site updates yet. "
                "Call from the number on the owner account."
            )
            code = "auth_insufficient"
        return {
            "ok": False,
            "denied": True,
            "created": False,
            "cancelled": False,
            "applied": False,
            "error": msg,
            "code": code,
            "auth_level": have,
            "required_level": need,
        }

    # Extra: enroll required + write tool + not enrolled
    if (
        enroll_required_for_write()
        and TOOL_MIN_LEVEL.get(name) in ("voice_soft", "voice_hard")
        and not getattr(state, "voice_enrolled", False)
        and voice_auth_vendor() not in ("", "none", "off", "stub")
    ):
        return {
            "ok": False,
            "denied": True,
            "error": (
                "I still need a short voice enrollment before I can change the site. "
                "We can do that on this call."
            ),
            "code": "enroll_required",
            "auth_level": have,
            "required_level": need,
        }

    # High-risk + anomaly step-up (after F1/F2 satisfied)
    need_step = False
    step_reason = ""
    if step_up_enabled() and not getattr(state, "step_up_ok", False):
        if name in STEP_UP_TOOLS:
            need_step = True
            step_reason = "high_risk"
        elif getattr(state, "auth_require_step_up", False) and name in ANOMALY_STEP_UP_TOOLS:
            need_step = True
            flags = list(getattr(state, "auth_anomaly_flags", []) or [])
            step_reason = ",".join(flags) or "anomaly"

    if need_step:
        return {
            "ok": False,
            "denied": True,
            "created": False,
            "cancelled": False,
            "applied": False,
            "error": (
                "That change needs a quick extra check — "
                "I can text a code to this phone. "
                "Use request_step_up_code, then verify_step_up_code with the digits."
            ),
            "code": "step_up_required",
            "auth_level": have,
            "required_level": need,
            "step_up_reason": step_reason,
            "anomaly_flags": list(getattr(state, "auth_anomaly_flags", []) or []),
        }

    return None


def step_up_enabled() -> bool:
    return (os.getenv("VOICE_STEP_UP_ENABLED") or "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def step_up_ttl_s() -> int:
    try:
        return max(60, int(os.getenv("VOICE_STEP_UP_TTL_S", "600") or "600"))
    except ValueError:
        return 600


def step_up_max_attempts() -> int:
    try:
        return max(1, int(os.getenv("VOICE_STEP_UP_MAX_ATTEMPTS", "5") or "5"))
    except ValueError:
        return 5


def _hash_code(code: str, salt: str) -> str:
    import hashlib

    return hashlib.sha256(f"{salt}:{code}".encode("utf-8")).hexdigest()


def request_step_up_code(
    state: Any,
    *,
    send_sms_fn: Any | None = None,
) -> dict[str, Any]:
    """Generate OTP, store hash on state, optionally SMS via send_sms_fn(to, body)."""
    import secrets
    import time

    if not step_up_enabled():
        state.step_up_ok = True
        return {"ok": True, "skipped": True, "reason": "step_up_disabled", "step_up_ok": True}

    phone = (getattr(state, "caller_number", None) or "").strip()
    if not phone:
        return {"ok": False, "error": "no caller phone for step-up SMS", "code": "phone_required"}

    # Rate-limit resend: 30s
    now = time.time()
    last = float(getattr(state, "step_up_sent_at", 0) or 0)
    if last and now - last < 30 and getattr(state, "step_up_code_hash", ""):
        return {
            "ok": True,
            "resent": False,
            "message": "A code was already sent recently — check your texts.",
            "retry_after_s": int(30 - (now - last)),
        }

    code = f"{secrets.randbelow(1_000_000):06d}"
    salt = (getattr(state, "call_sid", None) or phone) + str(int(now))
    state.step_up_code_hash = _hash_code(code, salt)
    state.step_up_salt = salt  # type: ignore[attr-defined]
    state.step_up_expires_at = now + step_up_ttl_s()
    state.step_up_attempts = 0
    state.step_up_sent_at = now
    state.step_up_ok = False

    body = (
        f"Florida Man site updates code: {code}. "
        f"Valid {step_up_ttl_s() // 60} min. If you didn't ask, ignore."
    )
    sms_ok = True
    sms_error = ""
    if send_sms_fn is not None:
        try:
            send_sms_fn(phone, body)
        except Exception as e:  # noqa: BLE001
            sms_ok = False
            sms_error = str(e)
            log.warning("step-up SMS failed: %s", e)
    else:
        # Dev/test: attach code only when explicitly allowed
        if (os.getenv("VOICE_STEP_UP_DEBUG_CODE") or "").strip().lower() in (
            "1",
            "true",
            "yes",
        ):
            return {
                "ok": True,
                "sent": False,
                "debug_code": code,
                "message": "Step-up code generated (debug mode, not SMSed).",
            }

    if not sms_ok:
        return {
            "ok": False,
            "error": f"Could not text the code: {sms_error}",
            "code": "sms_failed",
        }
    return {
        "ok": True,
        "sent": True,
        "message": "I texted a 6-digit code to this phone. What are the digits?",
        "to_last4": phone[-4:] if len(phone) >= 4 else "",
    }


def verify_step_up_code(state: Any, code: str) -> dict[str, Any]:
    """Validate OTP; set step_up_ok on success."""
    import time

    if not step_up_enabled():
        state.step_up_ok = True
        return {"ok": True, "step_up_ok": True, "skipped": True}

    raw = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(raw) < 4:
        return {"ok": False, "error": "That doesn't look like the code — try the 6 digits.", "code": "bad_format"}

    if getattr(state, "auth_level", "") == "locked":
        return {"ok": False, "error": "This line is locked.", "code": "auth_locked"}

    now = time.time()
    exp = float(getattr(state, "step_up_expires_at", 0) or 0)
    if not getattr(state, "step_up_code_hash", "") or not exp:
        return {
            "ok": False,
            "error": "No code is pending — ask me to text a new one.",
            "code": "no_pending",
        }
    if now > exp:
        return {
            "ok": False,
            "error": "That code expired — I can text a new one.",
            "code": "expired",
        }

    attempts = int(getattr(state, "step_up_attempts", 0) or 0) + 1
    state.step_up_attempts = attempts
    if attempts > step_up_max_attempts():
        state.auth_level = "locked"
        state.step_up_code_hash = ""
        return {
            "ok": False,
            "error": "Too many tries — this line is locked for updates on this call.",
            "code": "locked",
        }

    salt = getattr(state, "step_up_salt", "") or ""
    if _hash_code(raw, salt) != state.step_up_code_hash:
        left = step_up_max_attempts() - attempts
        return {
            "ok": False,
            "error": f"That code didn't match. {left} tries left." if left > 0 else "That code didn't match.",
            "code": "mismatch",
            "attempts": attempts,
        }

    state.step_up_ok = True
    state.step_up_code_hash = ""
    state.step_up_salt = ""  # type: ignore[attr-defined]
    return {
        "ok": True,
        "step_up_ok": True,
        "message": "Code confirmed — I can apply the change now.",
    }


def note_speech_activity(state: Any, *, force: bool = False, pcm: bytes | None = None) -> dict[str, Any]:
    """Throttle-friendly entry from realtime: one F2 window per utterance/cadence."""
    import time

    vendor = voice_auth_vendor()
    if vendor in ("", "none", "off", "stub"):
        return {"ok": True, "skipped": True, "reason": "vendor_none"}

    mode = (getattr(state, "mode", None) or "").strip().lower()
    # Only score on owner-capable calls
    if mode and mode not in ("owner_updates", "unified", "auto"):
        # auto still has mode set per call by resolve_call_mode
        pass
    if mode in ("sales", "ai411", "onboarding"):
        return {"ok": True, "skipped": True, "reason": "mode"}

    now = time.time()
    last = float(getattr(state, "voice_auth_last_window_at", 0) or 0)
    min_gap = float(os.getenv("VOICE_AUTH_WINDOW_GAP_S", "2.0") or "2.0")
    if not force and last and now - last < min_gap:
        return {"ok": True, "skipped": True, "reason": "throttled"}

    state.voice_auth_last_window_at = now
    return on_speech_window(state, pcm=pcm)


def deny_json(deny: dict[str, Any]) -> str:
    return json.dumps(deny, ensure_ascii=False)
