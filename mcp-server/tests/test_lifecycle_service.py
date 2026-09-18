from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import account_verification as verification
import lifecycle_service


def _auth():
    return verification._issue_auth_context(
        session_id="session-test",
        account_id="account-test",
        caller_transport_binding="transport-test",
        auth_revision=4,
        action="client_page_create",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        capability_id="cap-test",
        account_status="paid",
        owner_authenticated=True,
        proof_fresh=True,
    )


def test_capability_round_trip_is_audience_and_action_bound(monkeypatch):
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_SERVICE_KEY", "unit-test-key")
    token = lifecycle_service.mint_service_capability(
        _auth(), audience="fmws-account-lifecycle", ttl=30
    )
    ctx = lifecycle_service.context_from_capability(
        token,
        audience="fmws-account-lifecycle",
        actions={"client_page_create"},
    )
    assert verification.is_auth_context(ctx)
    assert ctx.account_id == "account-test"
    assert lifecycle_service.context_from_capability(
        token, audience="wrong-audience", actions={"client_page_create"}
    ) is None
    assert lifecycle_service.context_from_capability(
        token, audience="fmws-account-lifecycle", actions={"trusted_phone_add"}
    ) is None


def test_capability_fails_closed_without_key_or_after_tampering(monkeypatch):
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_SERVICE_KEY", "unit-test-key")
    token = lifecycle_service.mint_service_capability(
        _auth(), audience="fmws-account-lifecycle"
    )
    monkeypatch.delenv("ACCOUNT_LIFECYCLE_SERVICE_KEY")
    assert lifecycle_service.context_from_capability(
        token,
        audience="fmws-account-lifecycle",
        actions={"client_page_create"},
    ) is None
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_SERVICE_KEY", "unit-test-key")
    encoded, signature = token.split(".")
    altered = encoded[:-1] + ("A" if encoded[-1] != "A" else "B")
    assert lifecycle_service.context_from_capability(
        altered + "." + signature,
        audience="fmws-account-lifecycle",
        actions={"client_page_create"},
    ) is None


def test_mint_requires_fresh_owner_proof(monkeypatch):
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_SERVICE_KEY", "unit-test-key")
    auth = verification._issue_auth_context(
        session_id="s",
        account_id="a",
        caller_transport_binding="t",
        auth_revision=1,
        action="client_page_create",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        capability_id="c",
        account_status="paid",
        owner_authenticated=False,
        proof_fresh=False,
    )
    try:
        lifecycle_service.mint_service_capability(auth, audience="fmws-account-lifecycle")
    except ValueError as exc:
        assert "proof" in str(exc)
    else:
        raise AssertionError("unverified context minted a service capability")
