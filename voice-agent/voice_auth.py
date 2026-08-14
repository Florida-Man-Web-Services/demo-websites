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
    "send_sms_links": "cid_only",
    "log_call_outcome": "cid_only",
    # end_call is local and never gated here
}

# High-risk: need step_up_ok even if voice_hard (Phase 3+; listed for structure).
STEP_UP_TOOLS: frozenset[str] = frozenset(
    {
        # reserved: change_owner_phone, publish_site, ...
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
    return h >= n


def effective_min_level(tool_name: str) -> str:
    """Resolve tool minimum, degrading F2 requirements when vendor is stubbed."""
    nominal = TOOL_MIN_LEVEL.get(tool_name, "voice_soft")
    vendor = voice_auth_vendor()
    if vendor in ("", "none", "off", "stub") and not enroll_required_for_write():
        if nominal in ("voice_soft", "voice_hard"):
            # Phase 1: F1 is enough until speaker-verify ships.
            return "cid_only"
    if enroll_required_for_write() and nominal in ("voice_soft", "voice_hard"):
        # Force enrolled+soft path even if vendor still stubbed (deny until enrolled).
        return nominal
    return nominal


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

    if not phone:
        return {
            "auth_level": "anonymous",
            "voice_enrolled": False,
            "voice_score_ema": None,
            "voice_windows": 0,
            "step_up_ok": False,
            "matched_customers": [],
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
                for c in paid:
                    va = c.get("voice_auth") or {}
                    if isinstance(va, dict) and (
                        va.get("enrolled_at") or va.get("template_id")
                    ):
                        enrolled = True
                        break
            elif matched:
                # Registered but not paid — no owner write level.
                level = "anonymous"
            else:
                # Unknown number: soft F1 presence for legacy unclaimed CR path.
                level = "cid_legacy"
        except Exception as e:  # noqa: BLE001
            log.warning("compute_initial_auth customers path failed: %s", e)
            level = "cid_legacy"
    else:
        level = "cid_legacy"

    # If enrolled and vendor is live, we still start at cid_only and promote
    # via on_speech_window (Phase 2+). Phase 1 never auto-promotes to voice_*.
    return {
        "auth_level": level,
        "voice_enrolled": enrolled,
        "voice_score_ema": None,
        "voice_windows": 0,
        "step_up_ok": False,
        "matched_customers": matched,
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
    state.step_up_ok = bool(snap.get("step_up_ok"))


def refresh_auth(state: Any) -> str:
    """Recompute F1 level (e.g. after customer status changes mid-call)."""
    snap = compute_initial_auth(
        getattr(state, "caller_number", "") or "",
        getattr(state, "customer", None) or None,
    )
    # Preserve any F2 progress if we already promoted this call.
    prev = (getattr(state, "auth_level", None) or "anonymous").strip().lower()
    new = snap["auth_level"]
    if LEVEL_RANK.get(prev, 0) > LEVEL_RANK.get(new, 0) and prev not in (
        "locked",
        "anonymous",
    ):
        # Keep higher F2 level if already promoted.
        snap["auth_level"] = prev
        snap["voice_score_ema"] = getattr(state, "voice_score_ema", None)
        snap["voice_windows"] = getattr(state, "voice_windows", 0)
        snap["step_up_ok"] = getattr(state, "step_up_ok", False)
        snap["voice_enrolled"] = getattr(state, "voice_enrolled", False) or snap[
            "voice_enrolled"
        ]
    apply_auth_to_state(state, snap)
    return state.auth_level


def on_speech_window(
    state: Any,
    *,
    pcm: bytes | None = None,
    sample_rate: int = 8000,
) -> dict[str, Any]:
    """Phase 2+ hook: score a speech window and maybe promote auth_level.

    Phase 1 stub: no-op unless VOICE_AUTH_VENDOR is not none (still stub score).
    """
    vendor = voice_auth_vendor()
    if vendor in ("", "none", "off", "stub"):
        return {
            "ok": True,
            "skipped": True,
            "reason": "vendor_none",
            "auth_level": getattr(state, "auth_level", "anonymous"),
        }
    # Placeholder adapter — real SV backends plug in here.
    windows = int(getattr(state, "voice_windows", 0) or 0) + 1
    state.voice_windows = windows
    # Fake score path for future tests with vendor=mock
    if vendor == "mock":
        score = 0.9
        ema = getattr(state, "voice_score_ema", None)
        state.voice_score_ema = score if ema is None else (0.6 * float(ema) + 0.4 * score)
        soft = float(os.getenv("VOICE_AUTH_SOFT", "0.75"))
        hard = float(os.getenv("VOICE_AUTH_HARD", "0.85"))
        min_w = int(os.getenv("VOICE_AUTH_MIN_WINDOWS", "3"))
        if windows >= min_w and state.voice_score_ema >= hard:
            state.auth_level = "voice_hard"
        elif windows >= min_w and state.voice_score_ema >= soft:
            if LEVEL_RANK.get(getattr(state, "auth_level", ""), 0) < LEVEL_RANK["voice_soft"]:
                state.auth_level = "voice_soft"
        return {
            "ok": True,
            "score": score,
            "auth_level": state.auth_level,
            "windows": windows,
        }
    return {
        "ok": False,
        "error": f"voice auth vendor {vendor!r} not implemented",
        "auth_level": getattr(state, "auth_level", "anonymous"),
    }


def check_tool_allowed(state: Any, tool_name: str) -> dict[str, Any] | None:
    """Return speakable deny dict if tool blocked; None if allowed."""
    name = (tool_name or "").strip()
    if not name or name == "end_call":
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

    if name in STEP_UP_TOOLS and not getattr(state, "step_up_ok", False):
        return {
            "ok": False,
            "denied": True,
            "error": (
                "That change needs a quick extra check — "
                "I can text a code to this phone."
            ),
            "code": "step_up_required",
            "auth_level": have,
            "required_level": need,
        }

    if level_at_least(have, need):
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
        return None

    # Speakable, non-scary denies
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


def deny_json(deny: dict[str, Any]) -> str:
    return json.dumps(deny, ensure_ascii=False)
