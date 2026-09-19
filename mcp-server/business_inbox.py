"""Owner inbox for visitor/front-desk requests. Independent of CMS documents."""

from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Any

from business_cms_schema import canonical_slug
from business_cms_store import CmsStoreError, cms_enabled, tenant_lock

KINDS = frozenset({"message", "appointment", "callback", "contact"})
STATUSES = frozenset({"new", "acknowledged", "closed"})
MAX_BODY = 2_000
_ID_RE = re.compile(r"^req-[a-f0-9]{16}$")


class InboxError(RuntimeError):
    def __init__(self, code: str, message: str = "inbox error"):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sanitize_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise InboxError("invalid_body", f"{field} must be text")
    text = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) > MAX_BODY:
        raise InboxError("too_long", f"{field} is too long")
    return text


def create_request(
    slug: str,
    *,
    kind: str,
    message: str,
    source: str,
    consent_callback: bool = False,
    contact_ref: str | None = None,
    service_id: str | None = None,
    requested_time_text: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    if not cms_enabled():
        raise InboxError("feature_disabled")
    slug = canonical_slug(slug)
    if kind not in KINDS:
        raise InboxError("invalid_kind")
    body = _sanitize_text(message, field="message")
    key = (idempotency_key or "").strip() or secrets.token_hex(8)
    if len(key) > 80:
        raise InboxError("invalid_key")
    with tenant_lock(slug) as base:
        inbox = base / "inbox"
        inbox.mkdir(exist_ok=True)
        for existing in inbox.glob("req-*.json"):
            try:
                data = json.loads(existing.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("idempotency_key") == key:
                return {"ok": True, "request": _public_owner_view(data), "duplicate": True}
        request_id = f"req-{secrets.token_hex(8)}"
        record = {
            "id": request_id,
            "slug": slug,
            "kind": kind,
            "source": source,
            "message": body,
            "consent_callback": bool(consent_callback),
            "contact_ref": contact_ref if consent_callback else None,
            "service_id": service_id,
            "requested_time_text": requested_time_text,
            "status": "new",
            "created_at": _now(),
            "idempotency_key": key,
        }
        path = inbox / f"{request_id}.json"
        tmp = inbox / f".tmp-{request_id}"
        tmp.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
        return {"ok": True, "request": _public_owner_view(record), "duplicate": False}


def _public_owner_view(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["id"],
        "kind": record["kind"],
        "source": record.get("source"),
        "message": record.get("message"),
        "consent_callback": bool(record.get("consent_callback")),
        "has_contact_ref": bool(record.get("contact_ref")),
        "service_id": record.get("service_id"),
        "requested_time_text": record.get("requested_time_text"),
        "status": record.get("status"),
        "created_at": record.get("created_at"),
    }


def list_requests(slug: str) -> list[dict[str, Any]]:
    slug = canonical_slug(slug)
    with tenant_lock(slug) as base:
        inbox = base / "inbox"
        if not inbox.is_dir():
            return []
        rows = []
        for path in sorted(inbox.glob("req-*.json")):
            try:
                rows.append(_public_owner_view(json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, json.JSONDecodeError):
                continue
        return rows


def _load_raw(slug: str, request_id: str) -> tuple[Any, dict[str, Any]]:
    if not _ID_RE.match(request_id or ""):
        raise InboxError("invalid_id")
    base = None
    return base, {}  # replaced in callers using lock


def patch_request(slug: str, request_id: str, *, status: str) -> dict[str, Any]:
    if status not in STATUSES:
        raise InboxError("invalid_status")
    if not _ID_RE.match(request_id or ""):
        raise InboxError("invalid_id")
    with tenant_lock(slug) as base:
        path = base / "inbox" / f"{request_id}.json"
        if not path.is_file():
            raise InboxError("missing")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["status"] = status
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        return {"ok": True, "request": _public_owner_view(data)}


def delete_request(slug: str, request_id: str) -> dict[str, Any]:
    if not _ID_RE.match(request_id or ""):
        raise InboxError("invalid_id")
    with tenant_lock(slug) as base:
        path = base / "inbox" / f"{request_id}.json"
        if not path.is_file():
            raise InboxError("missing")
        path.unlink()
        return {"ok": True, "deleted": request_id}


def visitor_receipt(record: dict[str, Any]) -> dict[str, Any]:
    """Model-safe write receipt: no contact details."""
    return {
        "ok": True,
        "request_id": record["id"],
        "kind": record["kind"],
        "status": "pending_owner_review",
    }
