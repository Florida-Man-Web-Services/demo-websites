"""Stripe webhook receiver tests (G11/G18) — no network, no real Stripe."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time

import pytest

import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent
MCP_DIR = AGENT_DIR.parent / "mcp-server"
for p in (str(AGENT_DIR), str(MCP_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

TEST_SECRET = "whsec_test_123"


def _sig(payload: bytes, secret: str = TEST_SECRET, ts: int | None = None) -> str:
    t = int(time.time()) if ts is None else ts
    mac = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256)
    return f"t={t},v1={mac.hexdigest()}"


def _event(event_id="evt_1", type_="checkout.session.completed", phone="+1352555400"):
    return {
        "id": event_id,
        "type": type_,
        "data": {"object": {"client_reference_id": phone, "amount_total": 99900}},
    }


@pytest.fixture()
def billing_env(tmp_path, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", TEST_SECRET)
    monkeypatch.setenv("CALL_DB", str(tmp_path / "calls.db"))
    monkeypatch.setenv(
        "CUSTOMERS_PATH", str(tmp_path / "customers.json")
    )
    (tmp_path / "customers.json").write_text(
        json.dumps({"+1352555400": {"status": "sales_ready", "business_name": "Cafe"}}),
        encoding="utf-8",
    )
    import importlib

    import customers

    importlib.reload(customers)
    import billing

    importlib.reload(billing)
    yield billing, customers


def test_disabled_without_secret(billing_env, monkeypatch):
    billing, _ = billing_env
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET")
    assert billing.webhook_secret() == ""
    ok, reason = billing.verify_stripe_signature(b"{}", "t=1,v1=x", "")
    assert ok is False


def test_valid_signature_roundtrip(billing_env):
    billing, _ = billing_env
    payload = json.dumps(_event()).encode()
    ok, reason = billing.verify_stripe_signature(payload, _sig(payload), TEST_SECRET)
    assert ok is True, reason


def test_bad_signature_rejected(billing_env):
    billing, _ = billing_env
    payload = json.dumps(_event()).encode()
    ok, _ = billing.verify_stripe_signature(payload, _sig(payload, "whsec_wrong"), TEST_SECRET)
    assert ok is False
    ok, _ = billing.verify_stripe_signature(b"tampered", _sig(payload), TEST_SECRET)
    assert ok is False


def test_stale_timestamp_rejected(billing_env):
    billing, _ = billing_env
    payload = json.dumps(_event()).encode()
    old_ts = int(time.time()) - 3600
    ok, reason = billing.verify_stripe_signature(
        payload, _sig(payload, ts=old_ts), TEST_SECRET
    )
    assert ok is False and "tolerance" in reason


def test_activation_and_replay(billing_env):
    billing, customers = billing_env
    event = _event()
    first = billing.handle_stripe_event(event, customers)
    assert first["action"] == "activated"
    row = customers.get("+1352555400")
    assert row["status"] in ("paid", "active_owner")
    # Replay: same event id processed exactly once.
    second = billing.handle_stripe_event(event, customers)
    assert second["action"] == "duplicate"


def test_unmatched_payment_parked_pending(billing_env):
    billing, _ = billing_env
    event = _event(event_id="evt_2", phone="")
    result = billing.handle_stripe_event(event, customers=None)
    assert result["action"] == "pending_unmatched"


def test_refund_goes_to_operator_review(billing_env):
    billing, customers = billing_env
    event = _event(event_id="evt_3", type_="charge.refunded")
    result = billing.handle_stripe_event(event, customers)
    assert result["action"] == "operator_review"
    # Entitlement NOT auto-revoked.
    assert customers.get("+1352555400")["status"] == "sales_ready"


def test_unknown_event_ignored(billing_env):
    billing, _ = billing_env
    event = _event(event_id="evt_4", type_="invoice.paid")
    assert billing.handle_stripe_event(event, customers=None)["action"] == "ignored"
