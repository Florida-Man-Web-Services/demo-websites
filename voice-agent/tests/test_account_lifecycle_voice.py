"""Voice transport tests for the non-model lifecycle boundary."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mcp-server"))
from account_verification import FakeOTPAdapter, set_otp_adapter
import lifecycle_voice as lv


def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_ENABLED", "true")
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_DB", str(tmp_path / "lifecycle.sqlite3"))
    monkeypatch.setenv("CUSTOMERS_PATH", str(tmp_path / "customers.json"))
    (tmp_path / "customers.json").write_text(
        '{"+15551230000":{"status":"active_owner","account_id":"acct-1",'
        '"auth_revision":0,"phone":"+15551230000","trusted_phones":[]}}\n',
        encoding="utf-8",
    )
    return SimpleNamespace(
        call_sid="CA-VOICE-1",
        caller_number="+15551230000",
        customer={"account_id": "acct-1", "auth_revision": 0, "status": "active_owner"},
        lifecycle_auth=None,
    )


def test_voice_context_is_disabled_without_flag(monkeypatch):
    monkeypatch.delenv("ACCOUNT_LIFECYCLE_ENABLED", raising=False)
    state = SimpleNamespace(call_sid="CA-disabled", customer={"account_id": "acct-1", "auth_revision": 0, "status": "active_owner"})
    out = lv.create_auth_context(state, action="trusted_phone_add")
    assert out["state"] == "denied"
    assert out["code"] == "feature_disabled"


def test_voice_happy_path_keeps_private_values_out_of_results(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    adapter = FakeOTPAdapter()
    set_otp_adapter(adapter)
    try:
        auth = lv.create_auth_context(state, action="trusted_phone_add")
        assert not isinstance(auth, dict)
        sent = lv.request_owner_step_up(state, action="trusted_phone_add")
        assert sent["state"] == "verification_required"
        ref = lv.capture_private_secret_input(
            state, adapter.last_code_for_test(), purpose="owner_step_up",
            challenge_id=sent["challenge_id"],
        )
        verified = lv.complete_owner_step_up(
            state, challenge_id=sent["challenge_id"], secret_input_ref=ref
        )
        assert verified["state"] == "verified_success"
        assert "code" not in verified
        assert adapter.last_code_for_test() not in json.dumps(verified, default=str)

        destination_ref = lv.capture_private_secret_input(
            state, "+15551239999", purpose="destination_phone"
        )
        captured = lv.capture_account_phone(state, secret_input_ref=destination_ref)
        assert captured["state"] == "verified_success"
        assert "39999" not in json.dumps(captured)

        consent = lv.make_send_consent_event(state)
        sent_destination = lv.request_destination_verification(
            state, operation_id="op-private", consent_event=consent
        )
        assert sent_destination["state"] == "verification_required"
        code_ref = lv.capture_private_secret_input(
            state, adapter.last_code_for_test(), purpose="destination_phone",
            challenge_id=sent_destination["challenge_id"],
        )
        proof = lv.verify_destination(
            state, operation_id="op-private", challenge_id=sent_destination["challenge_id"],
            secret_input_ref=code_ref,
        )
        assert proof["state"] == "verified_success"
        assert "39999" not in json.dumps(proof)
    finally:
        set_otp_adapter(None)


def test_voice_confirmation_requires_keypad_event_and_rejects_replay(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    adapter = FakeOTPAdapter()
    set_otp_adapter(adapter)
    try:
        lv.create_auth_context(state, action="trusted_phone_add")
        started = lv.request_owner_step_up(state, action="trusted_phone_add")
        ref = lv.capture_private_secret_input(
            state, adapter.last_code_for_test(), purpose="owner_step_up",
            challenge_id=started["challenge_id"],
        )
        lv.complete_owner_step_up(state, challenge_id=started["challenge_id"], secret_input_ref=ref)

        from account_lifecycle import prepare_trusted_phone_add
        prepared = prepare_trusted_phone_add(ctx=state.lifecycle_auth, idempotency_key="voice-confirm")
        readback = lv.get_readback(state, operation_id=prepared["operation_id"])
        assert readback["state"] == "awaiting_confirmation"
        event = lv.make_keypad_event(state, digit="1")
        token = lv.capture_keypad_confirmation(
            state, operation_id=prepared["operation_id"],
            readback_digest=readback["readback_digest"], event=event,
        )
        assert isinstance(token, str)
        assert "39999" not in token
        replay = lv.capture_keypad_confirmation(
            state, operation_id=prepared["operation_id"],
            readback_digest=readback["readback_digest"], event=event,
        )
        assert replay["state"] == "denied"
        assert replay["code"] == "event_replayed"
        assert lv.make_keypad_event(state, digit="2")["code"] == "confirmation_cancelled"
    finally:
        set_otp_adapter(None)


def test_lifecycle_names_are_not_model_surface():
    assert "verify_destination_challenge" in lv.MODEL_LIFECYCLE_NAMES
    assert "capture_lifecycle_confirmation" in lv.MODEL_LIFECYCLE_NAMES
