"""Fail-closed account identity and lifecycle authorization tests."""

from datetime import datetime, timedelta, timezone
import importlib

import pytest

import account_lifecycle as lifecycle
import customers
from account_verification import _issue_auth_context


@pytest.fixture
def registry(tmp_path, monkeypatch):
    path = tmp_path / "customers.json"
    path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("CUSTOMERS_PATH", str(path))
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_DB", str(tmp_path / "lifecycle.sqlite3"))
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_ENABLED", "true")
    importlib.reload(customers)
    yield path
    importlib.reload(customers)


def make_context(account_id, *, action="trusted_phone_add", revision=0, **kwargs):
    return _issue_auth_context(
        session_id="session-1",
        account_id=account_id,
        caller_transport_binding="transport-1",
        auth_revision=revision,
        action=action,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        capability_id="cap-1",
        account_status="active_owner",
        owner_authenticated=True,
        proof_fresh=True,
        **kwargs,
    )


def test_identity_migration_is_stable_and_preserves_primary_map_key(registry):
    primary = "+13525550100"
    registry.write_text(
        '{"' + primary + '": {"id": "legacy", "phone": "' + primary
        + '", "status": "active_owner", "trusted_phones": []}}\n',
        encoding="utf-8",
    )

    first = customers.resolve_lifecycle_account(primary)
    second = customers.resolve_lifecycle_account(primary)
    data = customers._read()

    assert first["ok"] is True
    assert first["account_id"] == second["account_id"]
    assert first["auth_revision"] == second["auth_revision"] == 0
    assert list(data) == [primary]
    assert data[primary]["account_id"] == first["account_id"]


def test_resolve_requires_exactly_one_eligible_account(registry):
    primary = "+13525550100"
    customers.upsert(primary, status="callback_queued")
    assert customers.resolve_lifecycle_account(primary)["code"] == "account_unavailable"

    customers.upsert(primary, status="active_owner")
    assert customers.resolve_lifecycle_account(primary)["ok"] is True

    other = "+13525550101"
    customers.upsert(other, status="active_owner", patch={"trusted_phones": [primary]})
    ambiguous = customers.resolve_lifecycle_account(primary)
    assert ambiguous == {"ok": False, "code": "account_unavailable", "error": "account unavailable"}


def test_lifecycle_authorization_rejects_forged_missing_stale_forced_legacy_and_cross_account(registry):
    primary = "+13525550100"
    other = "+13525550101"
    owner = customers.upsert(primary, status="active_owner")["customer"]
    other_owner = customers.upsert(other, status="paid")["customer"]
    aid = owner["account_id"]

    assert customers.authorize_account_lifecycle(
        account_id=aid, action="trusted_phone_add", ctx={}
    )["code"] == "invalid_context"
    assert customers.authorize_account_lifecycle(
        account_id=aid,
        action="trusted_phone_add",
        ctx=make_context(other_owner["account_id"]),
    )["code"] == "account_unavailable"
    assert customers.authorize_account_lifecycle(
        account_id=aid,
        action="trusted_phone_add",
        ctx=make_context(aid, revision=1),
    )["code"] == "stale_auth_revision"
    assert customers.authorize_account_lifecycle(
        account_id=aid,
        action="trusted_phone_add",
        ctx=make_context(aid, forced_mode=True),
    )["code"] == "invalid_context"
    assert customers.authorize_account_lifecycle(
        account_id=aid,
        action="trusted_phone_add",
        ctx=make_context(aid, auth_level="cid_legacy"),
    )["code"] == "invalid_context"


def test_phone_refs_are_opaque_and_masked(registry):
    primary = "+13525550100"
    trusted = "+13525550102"
    owner = customers.upsert(primary, status="active_owner", patch={"trusted_phones": [trusted]})["customer"]
    ctx = make_context(owner["account_id"], action="lifecycle_phone_refs")
    result = lifecycle.lifecycle_phone_refs(owner["account_id"], ctx=ctx)
    encoded = str(result)
    assert result["ok"] is True
    assert all("phone_ref" in item and "••••" in item["label"] for item in result["phones"])
    assert primary not in encoded and trusted not in encoded


def test_phone_prepare_requires_registry_identity_and_rejects_raw_refs(registry):
    forged = make_context("acct_not_in_registry")
    missing = lifecycle.prepare_trusted_phone_add(ctx=forged, idempotency_key="missing-account")
    assert missing["state"] == "denied"
    assert missing["code"] == "account_unavailable"

    primary = "+13525550100"
    owner = customers.upsert(primary, status="active_owner")["customer"]
    raw_ref = "+13525550102"
    rejected = lifecycle.prepare_trusted_phone_remove(
        ctx=make_context(owner["account_id"], action="trusted_phone_remove"),
        phone_ref=raw_ref,
        idempotency_key="raw-ref",
    )
    assert rejected["state"] == "denied"
    assert raw_ref not in str(rejected)


def test_unrelated_duplicate_and_malformed_memberships_fail_closed(registry):
    primary = "+13525550100"
    other = "+13525550101"
    duplicate = "+13525550102"
    owner = customers.upsert(primary, status="active_owner")["customer"]
    customers.upsert(other, status="active_owner", patch={"trusted_phones": [duplicate]})
    customers.upsert("+13525550103", status="active_owner", patch={"trusted_phones": [duplicate]})
    duplicate_result = lifecycle.prepare_trusted_phone_add(
        ctx=make_context(owner["account_id"]), idempotency_key="duplicate-registry"
    )
    assert duplicate_result["code"] == "ambiguous_phone_membership"

    customers.upsert(other, patch={"trusted_phones": ["not-a-phone"]})
    malformed_result = lifecycle.prepare_trusted_phone_add(
        ctx=make_context(owner["account_id"]), idempotency_key="malformed-registry"
    )
    assert malformed_result["code"] == "ambiguous_phone_membership"
