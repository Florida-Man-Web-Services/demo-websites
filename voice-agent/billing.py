"""Stripe webhook reconciliation (Astra G11/G18).

DEFAULT-OFF: the endpoint 503s unless STRIPE_WEBHOOK_SECRET is set. Production
enablement is a deliberate operator step — never a code default.

Design:
- Signature: Stripe `Stripe-Signature: t=<ts>,v1=<hex>` scheme. HMAC-SHA256
  over f"{t}.{payload}" with the webhook secret; constant-time compare against
  every v1 tag; timestamp tolerance enforced (replay window).
- Replay safety: event ids are persisted in the call-log SQLite DB
  (`billing_events` table); a seen id is processed exactly once.
- Identity: the customer is resolved server-side from the event payload only
  (client_reference_id or metadata.phone). Browser return URLs are never
  trusted.
- Entitlements: successful payments activate the customer (same path as the
  manual mark-paid endpoint). Refunds/disputes are recorded and flagged for
  operator review — they never auto-revoke service.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sqlite3
import time
from typing import Any

log = logging.getLogger("billing")

DEFAULT_TOLERANCE_S = 300

_ACTIVATING_EVENTS = {"checkout.session.completed", "payment_intent.succeeded"}
_REVIEW_EVENTS = {"charge.refunded", "charge.dispute.created"}
_PENDING_KINDS = {"unmatched_payment"}


def webhook_secret() -> str:
    return (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()


def parse_signature_header(header: str) -> dict[str, list[str]]:
    """Parse `t=...,v1=...,v1=...` into {'t': [ts], 'v1': [hex, ...]}."""
    out: dict[str, list[str]] = {}
    for part in (header or "").split(","):
        piece = part.strip()
        if "=" not in piece:
            continue
        key, _, val = piece.partition("=")
        out.setdefault(key.strip(), []).append(val.strip())
    return out


def verify_stripe_signature(
    payload: bytes,
    header: str,
    secret: str,
    *,
    tolerance_s: int = DEFAULT_TOLERANCE_S,
    now: float | None = None,
) -> tuple[bool, str]:
    """Return (ok, reason). `reason` is operator-facing, never caller-facing."""
    if not secret:
        return False, "webhook secret not configured"
    parsed = parse_signature_header(header)
    ts_list = parsed.get("t") or []
    v1_list = parsed.get("v1") or []
    if not ts_list or not v1_list:
        return False, "missing signature fields"
    try:
        ts = int(ts_list[0])
    except ValueError:
        return False, "bad timestamp"
    now = time.time() if now is None else now
    if abs(now - ts) > tolerance_s:
        return False, "timestamp outside tolerance"
    expected = hmac.new(
        secret.encode("utf-8"), f"{ts}.".encode("utf-8") + payload, hashlib.sha256
    ).hexdigest()
    for tag in v1_list:
        if hmac.compare_digest(expected, tag):
            return True, "ok"
    return False, "signature mismatch"


def _db_path() -> str:
    return os.getenv("CALL_DB") or "call-log.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=10)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS billing_events (
            event_id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            received_at REAL NOT NULL,
            action TEXT NOT NULL,
            detail TEXT
        )
        """
    )
    return conn


def _already_seen(conn: sqlite3.Connection, event_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM billing_events WHERE event_id = ?", (event_id,)
    ).fetchone()
    return row is not None


def _record(
    conn: sqlite3.Connection,
    event_id: str,
    type_: str,
    action: str,
    detail: str = "",
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO billing_events (event_id, type, received_at, action, detail)"
        " VALUES (?, ?, ?, ?, ?)",
        (event_id, type_, time.time(), action, detail[:500]),
    )
    conn.commit()


def _extract_phone(obj: dict[str, Any]) -> str:
    for key in ("client_reference_id",):
        val = str(obj.get(key) or "").strip()
        if val:
            return val
    meta = obj.get("metadata") or {}
    val = str(meta.get("phone") or "").strip()
    return val


def handle_stripe_event(event: dict[str, Any], customers: Any) -> dict[str, Any]:
    """Route one verified Stripe event. Idempotent by event.id."""
    event_id = str(event.get("id") or "").strip()
    type_ = str(event.get("type") or "").strip()
    if not event_id or not type_:
        return {"action": "ignored", "reason": "missing id or type"}

    conn = _connect()
    try:
        if _already_seen(conn, event_id):
            return {"action": "duplicate", "event_id": event_id}

        obj = (event.get("data") or {}).get("object") or {}
        phone = _extract_phone(obj)

        if type_ in _ACTIVATING_EVENTS:
            if not phone:
                _record(conn, event_id, type_, "pending", "unmatched_payment")
                log.warning("stripe %s: no customer reference; parked pending", event_id)
                return {"action": "pending_unmatched", "event_id": event_id}
            result = customers.mark_paid(phone)
            if not result.get("ok"):
                _record(conn, event_id, type_, "failed", str(result.get("error")))
                return {"action": "failed", "error": result.get("error")}
            _record(conn, event_id, type_, "activated", phone)
            log.info("stripe %s activated %s", event_id, phone)
            return {"action": "activated", "phone": phone, "event_id": event_id}

        if type_ in _REVIEW_EVENTS:
            _record(conn, event_id, type_, "operator_review", phone)
            log.warning(
                "stripe %s (%s) needs operator review for %s", event_id, type_, phone
            )
            return {"action": "operator_review", "event_id": event_id}

        _record(conn, event_id, type_, "ignored", phone)
        return {"action": "ignored", "event_id": event_id}
    finally:
        conn.close()
