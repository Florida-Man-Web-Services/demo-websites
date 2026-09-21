"""Place ONE consented AI 411 onboarding callback.

Web-form signup is the consent. Never pass ?slug= — that forces sales mode.
Sales outreach stays on call.py (human-gated, slug required).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import config

log = logging.getLogger("voice-agent.callback_dial")

DIALABLE_STATUSES = frozenset({"callback_queued", "prospect", "onboarding"})
BLOCKED_STATUSES = frozenset({"resume_waitlist", "do_not_call", "churned"})


def callback_twiml_url(public_base_url: str | None = None) -> str:
    """TwiML URL for an onboarding callback — no sales slug query."""
    base = (
        public_base_url
        or os.getenv("PUBLIC_BASE_URL")
        or getattr(config, "PUBLIC_BASE_URL", "")
        or ""
    ).rstrip("/")
    return f"{base}/voice/outbound"


def _customers():
    import sys
    from pathlib import Path

    mcp = Path(__file__).resolve().parent.parent / "mcp-server"
    if str(mcp) not in sys.path:
        sys.path.insert(0, str(mcp))
    import customers  # noqa: PLC0415

    return customers


def place_onboarding_callback(phone: str, *, twilio=None) -> dict[str, Any]:
    """Create one Twilio call to a queued website-callback phone.

    Refuses resume_waitlist / unknown / non-queued statuses. Does not batch.
    """
    customers = _customers()
    key = customers.normalize_phone(phone)
    if not key:
        return {"ok": False, "error": "invalid phone"}
    row = customers.get(key)
    if not row:
        return {"ok": False, "error": "customer not found"}
    st = (row.get("status") or "").strip()
    src = (row.get("source") or "").strip()
    if st == "resume_waitlist" or src == "resume_web":
        return {"ok": False, "error": "resume waitlist is not a website callback"}
    if st in BLOCKED_STATUSES:
        return {"ok": False, "error": f"status {st} is not dialable"}
    if st not in DIALABLE_STATUSES:
        return {"ok": False, "error": f"status {st} is not a queued callback"}

    # Idempotency: never double-dial a phone that already has a recent
    # in-flight/recent callback. The marker is stored on the customer row so
    # it survives process restarts (durable dedupe, not in-process memory).
    recent = row.get("callback_last_call") or {}
    try:
        placed_at = float(recent.get("placed_at") or 0)
    except (TypeError, ValueError):
        placed_at = 0.0
    window = float(
        os.getenv("CALLBACK_DEDUPE_WINDOW_S")
        or getattr(config, "CALLBACK_DEDUPE_WINDOW_S", 24 * 3600)
    )
    if placed_at and (time.time() - placed_at) < window:
        prior_sid = str(recent.get("sid") or "")
        log.info("callback to %s already placed recently sid=%s", key, prior_sid)
        return {"ok": True, "already_placed": True, "sid": prior_sid, "to": key}

    url = callback_twiml_url()
    public = (
        os.getenv("PUBLIC_BASE_URL")
        or getattr(config, "PUBLIC_BASE_URL", "")
        or ""
    ).rstrip("/")
    status_cb = f"{public}/voice/status"
    from_number = os.getenv("TWILIO_PHONE_NUMBER") or config.TWILIO_PHONE_NUMBER
    if twilio is None:
        from agent import _twilio  # noqa: PLC0415

        twilio = _twilio()
    call = twilio.calls.create(
        to=key,
        from_=from_number,
        url=url,
        status_callback=status_cb,
        status_callback_event=["completed"],
    )
    sid = getattr(call, "sid", "") or ""
    log.info("onboarding callback placed to %s sid=%s", key, sid)
    try:
        customers.upsert(
            key,
            notes=f"Outbound callback placed {sid}",
            patch={
                "callback_sid": sid,
                "callback_last_call": {"sid": sid, "placed_at": time.time()},
            },
        )
    except Exception as e:  # noqa: BLE001
        log.warning("callback note failed: %s", e)
    return {"ok": True, "sid": sid, "to": key, "url": url}
