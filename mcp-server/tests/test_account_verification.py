"""Focused tests for the server-owned verification primitives."""

from datetime import datetime, timedelta, timezone

from account_verification import AuthContext, TrustedInputEvent, is_auth_context


def test_auth_context_and_trusted_input_event_are_typed_records():
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    auth = AuthContext(
        session_id="session-1",
        account_id="acct-1",
        caller_transport_binding="call-1",
        auth_revision=7,
        action="client_page_create",
        expires_at=expires_at,
        capability_id="capability-1",
        account_status="paid",
        owner_authenticated=True,
        proof_fresh=True,
    )
    event = TrustedInputEvent(
        event_id="event-1",
        session_id=auth.session_id,
        account_id=auth.account_id,
        caller_transport_binding=auth.caller_transport_binding,
        auth_revision=auth.auth_revision,
        action=auth.action,
        expires_at=expires_at,
        capability_id=auth.capability_id,
        event_type="keypad",
        value_digest="a" * 64,
    )

    assert is_auth_context(auth)
    assert auth.has_capability("client_page_create")
    assert not auth.has_capability("client_page_remove")
    assert event.has_capability("client_page_create")
    assert not hasattr(event, "secret_input")


def test_auth_context_check_rejects_duck_typed_lookalikes():
    assert not is_auth_context({"account_id": "acct-1"})
    assert not is_auth_context(type("Lookalike", (), {})())


def _ctx(*, session_id="session-1", action="trusted_phone_add", owner=False):
    return AuthContext(
        session_id=session_id,
        account_id="acct-1",
        caller_transport_binding="call-1",
        auth_revision=0,
        action=action,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        capability_id="cap-1",
        account_status="active_owner",
        owner_authenticated=owner,
        proof_fresh=owner,
    )


def test_lifecycle_verification_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ACCOUNT_LIFECYCLE_ENABLED", raising=False)
    from account_verification import begin_step_up

    result = begin_step_up(action="trusted_phone_add", ctx=_ctx())
    assert result["state"] == "denied"
    assert result["code"] == "feature_disabled"


def test_fake_owner_step_up_is_single_use_and_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_ENABLED", "true")
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_DB", str(tmp_path / "lifecycle.sqlite3"))
    monkeypatch.setenv("CUSTOMERS_PATH", str(tmp_path / "customers.json"))
    import customers
    from account_verification import (
        FakeOTPAdapter,
        begin_step_up,
        capture_secret_input,
        complete_step_up,
        set_otp_adapter,
    )

    (tmp_path / "customers.json").write_text(
        '{"+15551230000":{"status":"active_owner","account_id":"acct-1",'
        '"auth_revision":0,"phone":"+15551230000","trusted_phones":[]}}\n',
        encoding="utf-8",
    )
    import importlib
    importlib.reload(customers)
    adapter = FakeOTPAdapter()
    set_otp_adapter(adapter)
    try:
        ctx = _ctx()
        started = begin_step_up(action="trusted_phone_add", ctx=ctx)
        assert started["state"] == "verification_required"
        code = adapter.last_code_for_test()
        secret_ref = capture_secret_input(code, auth=ctx, purpose="owner_step_up")
        verified = complete_step_up(
            challenge_id=started["challenge_id"],
            secret_input_ref=secret_ref,
            ctx=ctx,
        )
        assert verified["state"] == "verified_success"
        assert verified["auth"].owner_authenticated is True
        assert code not in str(verified)
        assert "+15551230000" not in str(verified)
        replay = complete_step_up(
            challenge_id=started["challenge_id"],
            secret_input_ref=secret_ref,
            ctx=ctx,
        )
        assert replay["state"] == "denied"
    finally:
        set_otp_adapter(None)


def test_owner_step_up_wrong_code_expiry_purpose_and_session_are_denied(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_ENABLED", "true")
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_DB", str(tmp_path / "lifecycle.sqlite3"))
    monkeypatch.setenv("CUSTOMERS_PATH", str(tmp_path / "customers.json"))
    (tmp_path / "customers.json").write_text(
        '{"+15551230000":{"status":"active_owner","account_id":"acct-1",'
        '"auth_revision":0,"phone":"+15551230000","trusted_phones":[]}}\n',
        encoding="utf-8",
    )
    import customers
    import importlib
    importlib.reload(customers)
    from account_verification import (
        FakeOTPAdapter,
        begin_step_up,
        capture_secret_input,
        complete_step_up,
        set_otp_adapter,
    )

    adapter = FakeOTPAdapter()
    set_otp_adapter(adapter)
    try:
        ctx = _ctx()
        started = begin_step_up(action="trusted_phone_add", ctx=ctx)
        wrong = capture_secret_input("000000", auth=ctx, purpose="owner_step_up")
        bad = complete_step_up(challenge_id=started["challenge_id"], secret_input_ref=wrong, ctx=ctx)
        assert bad["state"] == "denied"
        assert bad["code"] in {"invalid_code", "attempts_exhausted"}

        other = _ctx(session_id="other-session")
        other_ref = capture_secret_input(adapter.last_code_for_test(), auth=other, purpose="owner_step_up")
        session_bad = complete_step_up(
            challenge_id=started["challenge_id"], secret_input_ref=other_ref, ctx=other
        )
        assert session_bad["state"] == "denied"
        assert session_bad["code"] == "invalid_context"

        purpose = capture_secret_input(adapter.last_code_for_test(), auth=ctx, purpose="destination_phone")
        purpose_bad = complete_step_up(
            challenge_id=started["challenge_id"], secret_input_ref=purpose, ctx=ctx
        )
        assert purpose_bad["state"] == "denied"
        assert purpose_bad["code"] == "wrong_purpose"
    finally:
        set_otp_adapter(None)


def test_destination_requires_private_capture_and_explicit_consent(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_ENABLED", "true")
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_DB", str(tmp_path / "lifecycle.sqlite3"))
    monkeypatch.setenv("CUSTOMERS_PATH", str(tmp_path / "customers.json"))
    (tmp_path / "customers.json").write_text(
        '{"+15551230000":{"status":"active_owner","account_id":"acct-1",'
        '"auth_revision":0,"phone":"+15551230000","trusted_phones":[]}}\n',
        encoding="utf-8",
    )
    import customers
    import importlib
    importlib.reload(customers)
    from account_verification import (
        FakeOTPAdapter,
        TrustedInputEvent,
        capture_account_phone,
        capture_secret_input,
        complete_step_up,
        request_destination_verification,
        request_owner_verification,
        set_otp_adapter,
        verify_destination_challenge,
    )

    adapter = FakeOTPAdapter()
    set_otp_adapter(adapter)
    try:
        base = _ctx()
        owner_challenge = request_owner_verification(auth=base, purpose="owner_step_up")
        owner_ref = capture_secret_input(adapter.last_code_for_test(), auth=base, purpose="owner_step_up")
        auth = complete_step_up(challenge_id=owner_challenge["challenge_id"], secret_input_ref=owner_ref, ctx=base)["auth"]
        destination_ref = capture_secret_input("+15551239999", auth=auth, purpose="destination_phone")
        captured = capture_account_phone(auth=auth, secret_input_ref=destination_ref)
        assert captured["state"] == "verified_success"
        assert "39999" not in str(captured)
        no_consent = request_destination_verification(auth=auth, operation_id="op_test", send_consent_event=None)
        assert no_consent["code"] == "consent_required"

        consent = TrustedInputEvent(
            event_id="consent-1", session_id=auth.session_id, account_id=auth.account_id,
            caller_transport_binding=auth.caller_transport_binding, auth_revision=auth.auth_revision,
            action=auth.action, expires_at=auth.expires_at, capability_id=auth.capability_id,
            event_type="send_consent",
        )
        sent = request_destination_verification(auth=auth, operation_id="op_test", send_consent_event=consent)
        assert sent["state"] == "verification_required"
        destination_code = adapter.last_code_for_test()
        code_ref = capture_secret_input(destination_code, auth=auth, purpose="destination_phone")
        proof = verify_destination_challenge(
            auth=auth, operation_id="op_test", challenge_id=sent["challenge_id"], secret_input_ref=code_ref
        )
        assert proof["state"] == "verified_success"
        assert "39999" not in str(proof)
    finally:
        set_otp_adapter(None)


def test_keypad_confirmation_is_bound_to_readback_and_single_use(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_ENABLED", "true")
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_DB", str(tmp_path / "lifecycle.sqlite3"))
    monkeypatch.setenv("CUSTOMERS_PATH", str(tmp_path / "customers.json"))
    (tmp_path / "customers.json").write_text(
        '{"+15551230000":{"status":"active_owner","account_id":"acct-1",'
        '"auth_revision":0,"phone":"+15551230000","trusted_phones":[]}}\n',
        encoding="utf-8",
    )
    import customers
    import importlib
    importlib.reload(customers)
    from account_lifecycle import prepare_trusted_phone_add
    from account_verification import (
        TrustedInputEvent,
        capture_lifecycle_confirmation,
        get_confirmation_readback,
    )

    auth = _ctx(owner=True)
    prepared = prepare_trusted_phone_add(ctx=auth, idempotency_key="confirm-1")
    assert prepared["state"] == "awaiting_confirmation"
    operation_id = prepared["operation_id"]
    readback = get_confirmation_readback(auth=auth, operation_id=operation_id)
    assert readback["state"] == "awaiting_confirmation"
    event = TrustedInputEvent(
        event_id="keypad-1", session_id=auth.session_id, account_id=auth.account_id,
        caller_transport_binding=auth.caller_transport_binding, auth_revision=auth.auth_revision,
        action=auth.action, expires_at=auth.expires_at, capability_id=auth.capability_id,
        event_type="keypad_confirm", value_digest="1" * 64,
    )
    token = capture_lifecycle_confirmation(
        auth=auth, operation_id=operation_id, readback_digest=readback["readback_digest"], event=event
    )
    assert isinstance(token, str) and token
    replay = capture_lifecycle_confirmation(
        auth=auth, operation_id=operation_id, readback_digest=readback["readback_digest"], event=event
    )
    assert isinstance(replay, dict) and replay["state"] == "denied"
    speech = TrustedInputEvent(
        event_id="speech-1", session_id=auth.session_id, account_id=auth.account_id,
        caller_transport_binding=auth.caller_transport_binding, auth_revision=auth.auth_revision,
        action=auth.action, expires_at=auth.expires_at, capability_id=auth.capability_id,
        event_type="spoken_yes", value_digest="1" * 64,
    )
    denied = capture_lifecycle_confirmation(
        auth=auth, operation_id=operation_id, readback_digest=readback["readback_digest"], event=speech
    )
    assert denied["state"] == "denied"


def test_missing_sender_fails_closed_and_resend_is_throttled(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_ENABLED", "true")
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_DB", str(tmp_path / "lifecycle.sqlite3"))
    monkeypatch.setenv("CUSTOMERS_PATH", str(tmp_path / "customers.json"))
    (tmp_path / "customers.json").write_text(
        '{"+15551230000":{"status":"active_owner","account_id":"acct-1",'
        '"auth_revision":0,"phone":"+15551230000","trusted_phones":[]}}\n',
        encoding="utf-8",
    )
    import customers
    import importlib
    importlib.reload(customers)
    from account_verification import FakeOTPAdapter, begin_step_up, set_otp_adapter

    ctx = _ctx()
    set_otp_adapter(None)
    missing = begin_step_up(action="trusted_phone_add", ctx=ctx)
    assert missing["state"] == "failed"
    assert missing["code"] == "sender_unavailable"

    adapter = FakeOTPAdapter()
    set_otp_adapter(adapter)
    try:
        first = begin_step_up(action="trusted_phone_add", ctx=ctx)
        assert first["state"] == "verification_required"
        second = begin_step_up(action="trusted_phone_add", ctx=ctx)
        assert second["state"] == "denied"
        assert second["code"] == "throttled"
    finally:
        set_otp_adapter(None)


def test_expired_code_is_rejected_without_returning_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_ENABLED", "true")
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_DB", str(tmp_path / "lifecycle.sqlite3"))
    monkeypatch.setenv("CUSTOMERS_PATH", str(tmp_path / "customers.json"))
    (tmp_path / "customers.json").write_text(
        '{"+15551230000":{"status":"active_owner","account_id":"acct-1",'
        '"auth_revision":0,"phone":"+15551230000","trusted_phones":[]}}\n',
        encoding="utf-8",
    )
    import customers
    import importlib
    importlib.reload(customers)
    from account_verification import FakeOTPAdapter, begin_step_up, capture_secret_input, complete_step_up, set_otp_adapter
    import sqlite3

    adapter = FakeOTPAdapter()
    set_otp_adapter(adapter)
    try:
        ctx = _ctx()
        started = begin_step_up(action="trusted_phone_add", ctx=ctx)
        with sqlite3.connect(tmp_path / "lifecycle.sqlite3") as conn:
            conn.execute(
                "UPDATE verification_challenges SET expires_at=? WHERE challenge_id=?",
                ("2000-01-01T00:00:00+00:00", started["challenge_id"]),
            )
            conn.commit()
        ref = capture_secret_input(adapter.last_code_for_test(), auth=ctx, purpose="owner_step_up")
        expired = complete_step_up(challenge_id=started["challenge_id"], secret_input_ref=ref, ctx=ctx)
        assert expired["state"] == "denied"
        assert expired["code"] == "expired"
        assert adapter.last_code_for_test() not in str(expired)
    finally:
        set_otp_adapter(None)
