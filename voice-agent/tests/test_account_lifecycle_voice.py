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
        '{"+13555550000":{"status":"active_owner","account_id":"acct-1",'
        '"auth_revision":0,"phone":"+13555550000","trusted_phones":[]}}\n',
        encoding="utf-8",
    )
    import importlib
    import customers
    importlib.reload(customers)
    for name in (
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_PHONE_NUMBER",
        "PUBLIC_BASE_URL",
        "DEEPINFRA_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.setenv(name, "test")
    import server
    from businesses import Business

    server.CALLS.clear()
    return server._make_state(
        "CA-VOICE-1",
        Business(name="Test", category="", phone="+13555550000"),
        "inbound",
        "+13555550000",
    )


def test_voice_context_is_disabled_without_flag(monkeypatch):
    monkeypatch.delenv("ACCOUNT_LIFECYCLE_ENABLED", raising=False)
    state = SimpleNamespace(call_sid="CA-disabled", customer={"account_id": "acct-1", "auth_revision": 0, "status": "active_owner"})
    out = lv.create_auth_context(state, action="trusted_phone_add")
    assert out["state"] == "denied"
    assert out["code"] == "feature_disabled"


def test_voice_context_requires_server_issued_validated_transport_binding(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_ENABLED", "true")
    monkeypatch.setenv("CUSTOMERS_PATH", str(tmp_path / "customers.json"))
    (tmp_path / "customers.json").write_text(
        '{"+13555550000":{"status":"active_owner","account_id":"acct-1",'
        '"auth_revision":0,"phone":"+13555550000","trusted_phones":[]}}\n',
        encoding="utf-8",
    )
    import importlib
    import customers
    importlib.reload(customers)

    fabricated = SimpleNamespace(
        call_sid="CA-fabricated",
        customer={"account_id": "acct-1", "auth_revision": 0, "status": "active_owner"},
    )
    import voice_auth

    assert voice_auth.issue_lifecycle_transport_binding(fabricated) is None
    voice_auth.apply_auth_to_state(fabricated)
    assert getattr(fabricated, "lifecycle_transport_binding", None) is None
    denied = lv.create_auth_context(fabricated, action="trusted_phone_add")
    assert denied == {"ok": False, "state": "denied", "code": "transport_unavailable"}

    for name in (
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_PHONE_NUMBER",
        "PUBLIC_BASE_URL",
        "DEEPINFRA_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.setenv(name, "test")
    import server
    from businesses import Business

    server.CALLS.clear()
    try:
        state = server._make_state(
            "CA-server-issued",
            Business(name="Test", category="", phone="+13555550000"),
            "inbound",
            "+13555550000",
        )
        assert server.CALLS.get(state.call_sid) is state
        assert lv.verification.validate_transport_session_binding(
            state.lifecycle_transport_binding
        )
        auth = voice_auth.lifecycle_auth_context(state, action="trusted_phone_add")
        assert lv.verification.is_auth_context(auth)
        assert auth.session_id == "voice:CA-server-issued"
        forged = SimpleNamespace(
            call_sid=state.call_sid,
            customer=dict(state.customer),
            lifecycle_transport_binding=state.lifecycle_transport_binding,
        )
        assert voice_auth.issue_lifecycle_transport_binding(forged) is None
        assert lv.create_auth_context(forged, action="trusted_phone_add")["code"] == "transport_unavailable"
    finally:
        server.CALLS.clear()


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
            state, "+13555559999", purpose="destination_phone"
        )
        captured = lv.capture_account_phone(state, secret_input_ref=destination_ref)
        assert captured["state"] == "verified_success"
        assert "59999" not in json.dumps(captured)

        consent = lv.make_send_consent_event(state, operation_id="op-private")
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
        assert "59999" not in json.dumps(proof)
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
        event = lv.make_keypad_event(
            state, digit="1", operation_id=prepared["operation_id"]
        )
        token = lv.capture_keypad_confirmation(
            state, operation_id=prepared["operation_id"],
            readback_digest=readback["readback_digest"], event=event,
        )
        assert isinstance(token, str)
        assert "59999" not in token
        replay = lv.capture_keypad_confirmation(
            state, operation_id=prepared["operation_id"],
            readback_digest=readback["readback_digest"], event=event,
        )
        assert replay["state"] == "denied"
        assert replay["code"] == "event_replayed"
        assert lv.make_keypad_event(state, digit="2")["code"] == "confirmation_cancelled"
    finally:
        set_otp_adapter(None)


def test_keypad_cancel_revokes_readback_and_blocks_commit(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    adapter = FakeOTPAdapter()
    set_otp_adapter(adapter)
    try:
        lv.create_auth_context(state, action="trusted_phone_add")
        started = lv.request_owner_step_up(state, action="trusted_phone_add")
        secret_ref = lv.capture_private_secret_input(
            state, adapter.last_code_for_test(), purpose="owner_step_up",
            challenge_id=started["challenge_id"],
        )
        lv.complete_owner_step_up(
            state, challenge_id=started["challenge_id"], secret_input_ref=secret_ref
        )
        from account_lifecycle import commit_account_operation, prepare_trusted_phone_add

        prepared = prepare_trusted_phone_add(
            ctx=state.lifecycle_auth, idempotency_key="voice-cancel"
        )
        operation_id = prepared["operation_id"]
        readback = lv.get_readback(state, operation_id=operation_id)
        cancelled = lv.make_keypad_event(state, digit="2", operation_id=operation_id)
        assert cancelled["code"] == "confirmation_cancelled"

        later_readback = lv.get_readback(state, operation_id=operation_id)
        assert later_readback["state"] == "denied"
        event = lv.make_keypad_event(state, digit="1", operation_id=operation_id)
        denied = lv.capture_keypad_confirmation(
            state,
            operation_id=operation_id,
            readback_digest=readback["readback_digest"],
            event=event,
        )
        assert denied["state"] == "denied"
        committed = commit_account_operation(
            ctx=state.lifecycle_auth,
            operation_id=operation_id,
            confirmation_token="confirmation_never_issued",
        )
        assert committed["state"] == "verified_noop"
        assert committed["code"] == "cancelled"
    finally:
        set_otp_adapter(None)


def test_lifecycle_names_are_not_model_surface():
    assert "verify_destination_challenge" in lv.MODEL_LIFECYCLE_NAMES
    assert "capture_lifecycle_confirmation" in lv.MODEL_LIFECYCLE_NAMES
