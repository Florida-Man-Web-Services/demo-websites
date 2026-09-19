"""Published-only front-desk knowledge and request tools.

Tenant slug, session identity, callback reference, and idempotency key are
injected by the trusted runtime — never model-selectable.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from business_cms_schema import canonical_slug
from business_cms_store import cms_enabled, current_release
import business_inbox as inbox

SECTIONS = frozenset({"identity", "hours", "services", "faq", "page", "forms", "all"})
_FRONT_DESK_FLAG = "FRONT_DESK_ENABLED"


def front_desk_enabled() -> bool:
    return (os.getenv(_FRONT_DESK_FLAG) or "").strip().lower() in {"1", "true", "yes", "on"}


class FrontDeskError(RuntimeError):
    def __init__(self, code: str, message: str = "front desk unavailable"):
        super().__init__(message)
        self.code = code


def _require_published(slug: str, release_id: str | None) -> dict[str, Any]:
    if not cms_enabled() or not front_desk_enabled():
        raise FrontDeskError("feature_disabled")
    slug = canonical_slug(slug)
    live = current_release(slug)
    if live is None or not live.get("public"):
        raise FrontDeskError("unpublished")
    if release_id and live["release_id"] != release_id:
        # Session is pinned to an older release still on disk.
        folder = live["folder"].parent / release_id
        public_path = folder / "public.json"
        if not public_path.is_file():
            raise FrontDeskError("stale_session")
        import json

        return {"release_id": release_id, "public": json.loads(public_path.read_text(encoding="utf-8"))}
    return live


def get_business(slug: str, section: str = "all", *, release_id: str | None = None) -> dict[str, Any]:
    live = _require_published(slug, release_id)
    public = live["public"]
    if section not in SECTIONS:
        raise FrontDeskError("invalid_section")
    payload = public if section == "all" else {section: public.get(section), "slug": public.get("slug")}
    return {"ok": True, "release_id": live["release_id"], "section": section, "data": payload}


def hours_status(public: dict[str, Any], *, at: datetime | None = None) -> dict[str, Any]:
    hours = public.get("hours") or {}
    if hours.get("unknown") or not hours.get("timezone"):
        return {"known": False, "open": None, "reason": "hours_unknown"}
    try:
        tz = ZoneInfo(hours["timezone"])
    except Exception:
        return {"known": False, "open": None, "reason": "hours_unknown"}
    now = at.astimezone(tz) if at is not None else datetime.now(tz)
    date_key = now.date().isoformat()
    for exc in hours.get("exceptions") or []:
        if exc.get("date") == date_key:
            if exc.get("closed"):
                return {"known": True, "open": False, "reason": "exception_closed"}
            return _open_from_intervals(now, exc.get("intervals") or [])
    weekday = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][now.weekday()]
    return _open_from_intervals(now, (hours.get("weekly") or {}).get(weekday) or [])


def _minutes(stamp: str) -> int:
    hour, minute = stamp.split(":")
    return int(hour) * 60 + int(minute)


def _open_from_intervals(now: datetime, intervals: list[dict[str, str]]) -> dict[str, Any]:
    if not intervals:
        return {"known": True, "open": False, "reason": "closed"}
    current = now.hour * 60 + now.minute
    for item in intervals:
        start = _minutes(item["open"])
        end = _minutes(item["close"])
        if end <= start:
            # overnight
            if current >= start or current < end:
                return {"known": True, "open": True, "reason": "open"}
        elif start <= current < end:
            return {"known": True, "open": True, "reason": "open"}
    return {"known": True, "open": False, "reason": "closed"}


def leave_message(
    slug: str,
    message: str,
    *,
    use_caller_callback: bool,
    contact_ref: str | None,
    idempotency_key: str,
    source: str = "front_desk",
) -> dict[str, Any]:
    _require_published(slug, None)
    result = inbox.create_request(
        slug,
        kind="message",
        message=message,
        source=source,
        consent_callback=use_caller_callback,
        contact_ref=contact_ref if use_caller_callback else None,
        idempotency_key=idempotency_key,
    )
    return inbox.visitor_receipt(result["request"])


def request_appointment(
    slug: str,
    *,
    service_id: str | None,
    requested_time_text: str,
    notes: str,
    use_caller_callback: bool,
    contact_ref: str | None,
    idempotency_key: str,
) -> dict[str, Any]:
    live = _require_published(slug, None)
    forms = (live["public"].get("forms") or {}).get("appointment") or {}
    if not forms.get("enabled", True):
        raise FrontDeskError("form_disabled")
    result = inbox.create_request(
        slug,
        kind="appointment",
        message=notes or requested_time_text,
        source="front_desk",
        consent_callback=use_caller_callback,
        contact_ref=contact_ref if use_caller_callback else None,
        service_id=service_id,
        requested_time_text=requested_time_text,
        idempotency_key=idempotency_key,
    )
    receipt = inbox.visitor_receipt(result["request"])
    receipt["booking"] = False
    receipt["status"] = "pending_owner_review"
    return receipt


def request_owner_callback(
    slug: str,
    *,
    reason: str,
    use_caller_callback: bool,
    contact_ref: str | None,
    idempotency_key: str,
) -> dict[str, Any]:
    _require_published(slug, None)
    if not use_caller_callback or not contact_ref:
        raise FrontDeskError("callback_contact_required")
    result = inbox.create_request(
        slug,
        kind="callback",
        message=reason,
        source="front_desk",
        consent_callback=True,
        contact_ref=contact_ref,
        idempotency_key=idempotency_key,
    )
    receipt = inbox.visitor_receipt(result["request"])
    receipt["live_transfer"] = False
    return receipt
