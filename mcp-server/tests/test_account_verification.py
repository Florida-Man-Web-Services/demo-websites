"""Focused tests for the server-owned verification primitives."""

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

import account_verification as verification

from account_verification import (
    AuthContext,
    TrustedInputEvent,
    _issue_auth_context,
    issue_auth_context,
    _issue_trusted_input_event,
    issue_trusted_input_event,
    is_auth_context,
    is_trusted_input_event,
)


def test_auth_context_and_trusted_input_event_are_typed_records():
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    auth = _issue_auth_context(
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
    event = _issue_trusted_input_event(
        auth=auth, event_type="keypad_confirm", operation_id="op-1",
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
    return _issue_auth_context(
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
        '{"+13555550000":{"status":"active_owner","account_id":"acct-1",'
        '"auth_revision":0,"phone":"+13555550000","trusted_phones":[]}}\n',
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
        assert "+13555550000" not in str(verified)
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
        '{"+13555550000":{"status":"active_owner","account_id":"acct-1",'
        '"auth_revision":0,"phone":"+13555550000","trusted_phones":[]}}\n',
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
        '{"+13555550000":{"status":"active_owner","account_id":"acct-1",'
        '"auth_revision":0,"phone":"+13555550000","trusted_phones":[]}}\n',
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
        destination_ref = capture_secret_input("+13555559999", auth=auth, purpose="destination_phone")
        captured = capture_account_phone(auth=auth, secret_input_ref=destination_ref)
        assert captured["state"] == "verified_success"
        assert "59999" not in str(captured)
        no_consent = request_destination_verification(auth=auth, operation_id="op_test", send_consent_event=None)
        assert no_consent["code"] == "consent_required"

        consent = _issue_trusted_input_event(
            auth=auth, event_type="send_consent", operation_id="op_test",
            )
        sent = request_destination_verification(auth=auth, operation_id="op_test", send_consent_event=consent)
        assert sent["state"] == "verification_required"
        destination_code = adapter.last_code_for_test()
        code_ref = capture_secret_input(destination_code, auth=auth, purpose="destination_phone")
        proof = verify_destination_challenge(
            auth=auth, operation_id="op_test", challenge_id=sent["challenge_id"], secret_input_ref=code_ref
        )
        assert proof["state"] == "verified_success"
        assert "59999" not in str(proof)
    finally:
        set_otp_adapter(None)


def test_keypad_confirmation_is_bound_to_readback_and_single_use(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_ENABLED", "true")
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_DB", str(tmp_path / "lifecycle.sqlite3"))
    monkeypatch.setenv("CUSTOMERS_PATH", str(tmp_path / "customers.json"))
    (tmp_path / "customers.json").write_text(
        '{"+13555550000":{"status":"active_owner","account_id":"acct-1",'
        '"auth_revision":0,"phone":"+13555550000","trusted_phones":[]}}\n',
        encoding="utf-8",
    )
    import customers
    import importlib
    importlib.reload(customers)
    from account_lifecycle import prepare_trusted_phone_add
    from account_verification import (
        _issue_trusted_input_event,
        capture_lifecycle_confirmation,
        get_confirmation_readback,
    )

    auth = _ctx(owner=True)
    prepared = prepare_trusted_phone_add(ctx=auth, idempotency_key="confirm-1")
    assert prepared["state"] == "awaiting_confirmation"
    operation_id = prepared["operation_id"]
    readback = get_confirmation_readback(auth=auth, operation_id=operation_id)
    assert readback["state"] == "awaiting_confirmation"
    event = _issue_trusted_input_event(
        auth=auth, event_type="keypad_confirm", operation_id=operation_id,
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
        '{"+13555550000":{"status":"active_owner","account_id":"acct-1",'
        '"auth_revision":0,"phone":"+13555550000","trusted_phones":[]}}\n',
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
        '{"+13555550000":{"status":"active_owner","account_id":"acct-1",'
        '"auth_revision":0,"phone":"+13555550000","trusted_phones":[]}}\n',
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


def test_public_construction_and_provenance_mutation_cannot_mint_authority():
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    direct = AuthContext(
        session_id="session-1", account_id="acct-1", caller_transport_binding="call-1",
        auth_revision=0, action="trusted_phone_add", expires_at=expires_at,
        capability_id="cap-1", account_status="active_owner",
        owner_authenticated=True, proof_fresh=True,
    )
    assert not is_auth_context(direct)
    with pytest.raises(TypeError):
        issue_auth_context(
            session_id="session-1", account_id="acct-1", caller_transport_binding="call-1",
            auth_revision=0, action="trusted_phone_add", expires_at=expires_at,
            capability_id="cap-1", account_status="active_owner",
            owner_authenticated=True, proof_fresh=True,
        )

    auth = _ctx(owner=True)
    with pytest.raises(TypeError):
        issue_trusted_input_event(auth=auth, event_type="keypad_confirm", operation_id="op-1")
    object.__setattr__(auth, "account_id", "attacker")
    assert not is_auth_context(auth)
    auth = _ctx(owner=True)
    event = _issue_trusted_input_event(
        auth=auth, event_type="keypad_confirm", operation_id="op-1",
    )
    object.__setattr__(event, "operation_id", "op-2")
    assert not is_trusted_input_event(event)


def test_otp_consumption_is_atomic_under_concurrent_replay(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_ENABLED", "true")
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_DB", str(tmp_path / "lifecycle.sqlite3"))
    monkeypatch.setenv("CUSTOMERS_PATH", str(tmp_path / "customers.json"))
    (tmp_path / "customers.json").write_text(
        '{"+13555550000":{"status":"active_owner","account_id":"acct-1",'
        '"auth_revision":0,"phone":"+13555550000","trusted_phones":[]}}\n',
        encoding="utf-8",
    )
    import customers
    import importlib
    importlib.reload(customers)
    from account_verification import FakeOTPAdapter, begin_step_up, capture_secret_input, complete_step_up, set_otp_adapter

    adapter = FakeOTPAdapter()
    set_otp_adapter(adapter)
    try:
        ctx = _ctx()
        started = begin_step_up(action="trusted_phone_add", ctx=ctx)
        ref = capture_secret_input(adapter.last_code_for_test(), auth=ctx, purpose="owner_step_up", challenge_id=started["challenge_id"])

        def consume_once():
            return complete_step_up(challenge_id=started["challenge_id"], secret_input_ref=ref, ctx=ctx)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: consume_once(), range(2)))
        assert sorted(result["state"] for result in results) == ["denied", "verified_success"]
        with sqlite3.connect(tmp_path / "lifecycle.sqlite3") as conn:
            assert conn.execute(
                "SELECT state, attempts FROM verification_challenges WHERE challenge_id=?",
                (started["challenge_id"],),
            ).fetchone() == ("consumed", 1)
    finally:
        set_otp_adapter(None)


def test_resend_supersedes_previous_challenge_and_revision_is_authoritative(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_ENABLED", "true")
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_DB", str(tmp_path / "lifecycle.sqlite3"))
    monkeypatch.setenv("CUSTOMERS_PATH", str(tmp_path / "customers.json"))
    (tmp_path / "customers.json").write_text(
        '{"+13555550000":{"status":"active_owner","account_id":"acct-1",'
        '"auth_revision":0,"phone":"+13555550000","trusted_phones":[]}}\n',
        encoding="utf-8",
    )
    import customers
    import importlib
    importlib.reload(customers)
    from account_verification import FakeOTPAdapter, begin_step_up, capture_secret_input, complete_step_up, set_otp_adapter

    adapter = FakeOTPAdapter()
    set_otp_adapter(adapter)
    try:
        ctx = _ctx()
        first = begin_step_up(action="trusted_phone_add", ctx=ctx)
        first_ref = capture_secret_input(adapter.last_code_for_test(), auth=ctx, purpose="owner_step_up", challenge_id=first["challenge_id"])
        with verification._PRIVATE_LOCK:
            verification._SEND_HISTORY.clear()
        second = begin_step_up(action="trusted_phone_add", ctx=ctx)
        assert len(adapter.sent) == 2
        assert adapter.sent[-1]["challenge_id"] == second["challenge_id"]
        with sqlite3.connect(tmp_path / "lifecycle.sqlite3") as conn:
            delivery_states = dict(conn.execute(
                "SELECT challenge_id, state FROM verification_challenges"
            ).fetchall())
        assert delivery_states[second["challenge_id"]] == "pending"
        old = complete_step_up(challenge_id=first["challenge_id"], secret_input_ref=first_ref, ctx=ctx)
        assert old == {"ok": False, "state": "denied", "code": "replayed_challenge"}
        second_ref = capture_secret_input(adapter.last_code_for_test(), auth=ctx, purpose="owner_step_up", challenge_id=second["challenge_id"])
        customers.upsert("+13555550000", patch={"auth_revision": 1})
        stale = complete_step_up(challenge_id=second["challenge_id"], secret_input_ref=second_ref, ctx=ctx)
        assert stale == {"ok": False, "state": "denied", "code": "stale_auth_revision"}
    finally:
        set_otp_adapter(None)


def test_sender_false_none_and_storage_errors_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_ENABLED", "true")
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_DB", str(tmp_path / "lifecycle.sqlite3"))
    monkeypatch.setenv("CUSTOMERS_PATH", str(tmp_path / "customers.json"))
    (tmp_path / "customers.json").write_text(
        '{"+13555550000":{"status":"active_owner","account_id":"acct-1",'
        '"auth_revision":0,"phone":"+13555550000","trusted_phones":[]}}\n',
        encoding="utf-8",
    )
    import customers
    import importlib
    importlib.reload(customers)
    from account_verification import begin_step_up, set_otp_adapter

    class Sender:
        def __init__(self, result):
            self.result = result
        def send(self, *_args, **_kwargs):
            return self.result

    ctx = _ctx()
    try:
        for result in (False, None):
            set_otp_adapter(Sender(result))
            with verification._PRIVATE_LOCK:
                verification._SEND_HISTORY.clear()
            failed = begin_step_up(action="trusted_phone_add", ctx=ctx)
            assert failed["state"] == "failed"
            assert failed["code"] == "sender_unavailable"
        set_otp_adapter(Sender(True))
        with verification._PRIVATE_LOCK:
            verification._SEND_HISTORY.clear()
        monkeypatch.setattr(verification, "_store", lambda: (_ for _ in ()).throw(sqlite3.DatabaseError("unavailable")))
        failed = begin_step_up(action="trusted_phone_add", ctx=ctx)
        assert failed["state"] == "failed"
        assert failed["code"] == "storage_unavailable"
    finally:
        set_otp_adapter(None)


def test_resend_database_failure_leaves_old_and_ambiguous_codes_unusable(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_ENABLED", "true")
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_DB", str(tmp_path / "lifecycle.sqlite3"))
    monkeypatch.setenv("CUSTOMERS_PATH", str(tmp_path / "customers.json"))
    (tmp_path / "customers.json").write_text(
        '{"+13555550000":{"status":"active_owner","account_id":"acct-1",'
        '"auth_revision":0,"phone":"+13555550000","trusted_phones":[]}}\n',
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
    original_store = verification._store
    calls = 0

    def fail_after_send():
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_store()
        raise sqlite3.DatabaseError("finalize unavailable")

    try:
        ctx = _ctx()
        first = begin_step_up(action="trusted_phone_add", ctx=ctx)
        old_ref = capture_secret_input(
            adapter.last_code_for_test(),
            auth=ctx,
            purpose="owner_step_up",
            challenge_id=first["challenge_id"],
        )
        with verification._PRIVATE_LOCK:
            verification._SEND_HISTORY.clear()
        monkeypatch.setattr(verification, "_store", fail_after_send)
        second = begin_step_up(action="trusted_phone_add", ctx=ctx)
        assert second["state"] == "failed"
        assert second["code"] == "storage_unavailable"

        monkeypatch.setattr(verification, "_store", original_store)
        old_result = complete_step_up(
            challenge_id=first["challenge_id"], secret_input_ref=old_ref, ctx=ctx
        )
        assert old_result["state"] == "denied"
        assert old_result["code"] == "replayed_challenge"
        with sqlite3.connect(tmp_path / "lifecycle.sqlite3") as conn:
            states = dict(conn.execute(
                "SELECT challenge_id, state FROM verification_challenges"
            ).fetchall())
        assert states[first["challenge_id"]] == "superseded"
        assert not any(state == "pending" for state in states.values())
    finally:
        monkeypatch.setattr(verification, "_store", original_store)
        set_otp_adapter(None)
