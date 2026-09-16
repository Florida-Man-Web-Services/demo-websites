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
