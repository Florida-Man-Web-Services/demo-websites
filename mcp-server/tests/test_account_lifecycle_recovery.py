"""Crash-boundary and reconciliation tests for phone operations."""

from datetime import datetime, timedelta, timezone
import importlib

import pytest

import account_lifecycle as lifecycle
import customers
from account_verification import AuthContext


@pytest.fixture
def setup(tmp_path, monkeypatch):
    registry = tmp_path / "customers.json"
    registry.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("CUSTOMERS_PATH", str(registry))
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_DB", str(tmp_path / "lifecycle.sqlite3"))
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_ENABLED", "true")
    lifecycle.set_failure_point(None)
    importlib.reload(customers)
    lifecycle._init_schema(tmp_path / "lifecycle.sqlite3")
    owner = customers.upsert("+13525550100", status="active_owner")["customer"]
    yield registry, owner
    lifecycle.set_failure_point(None)
    importlib.reload(customers)


def context(account_id, revision=0):
    return AuthContext(
        session_id="session-1", account_id=account_id,
        caller_transport_binding="transport-1", auth_revision=revision,
        action="trusted_phone_add",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        capability_id="cap-1", account_status="active_owner",
        owner_authenticated=True, proof_fresh=True,
    )


def test_after_registry_failure_is_pending_then_reconciles_exactly(setup):
    registry, owner = setup
    prepared = lifecycle.prepare_trusted_phone_add(
        ctx=context(owner["account_id"]), idempotency_key="recover-1"
    )
    lifecycle.set_failure_point("after_registry_replacement")
    pending = lifecycle.apply_trusted_phone_operation(
        operation_id=prepared["operation_id"], account_id=owner["account_id"],
        action="trusted_phone_add", phone_e164="+13525550102", expected_auth_revision=0,
    )
    assert pending["state"] == "pending_reconciliation"
    assert "+13525550102" in customers.get("+13525550100")["trusted_phones"]

    blocked_prepare = lifecycle.prepare_trusted_phone_add(
        ctx=context(owner["account_id"]), idempotency_key="recover-2"
    )
    assert blocked_prepare["state"] == "pending_reconciliation"
    lifecycle.set_failure_point(None)
    recovered = lifecycle.reconcile_pending_operations(
        account_id=owner["account_id"], operation_id=prepared["operation_id"]
    )
    assert recovered["ok"] is True
    assert recovered["operations"][0]["state"] == "verified_success"
    assert lifecycle.reconcile_pending_operations(account_id=owner["account_id"])["count"] == 0


def test_ambiguous_recovery_stays_blocked_and_never_reports_success(setup):
    registry, owner = setup
    prepared = lifecycle.prepare_trusted_phone_add(
        ctx=context(owner["account_id"]), idempotency_key="ambiguous-1"
    )
    lifecycle.set_failure_point("after_registry_replacement")
    pending = lifecycle.apply_trusted_phone_operation(
        operation_id=prepared["operation_id"], account_id=owner["account_id"],
        action="trusted_phone_add", phone_e164="+13525550102", expected_auth_revision=0,
    )
    assert pending["state"] == "pending_reconciliation"
    lifecycle.set_failure_point(None)
    # A second untracked registry change makes the pending revision ambiguous.
    customers.upsert("+13525550100", patch={"auth_revision": 2})

    recovered = lifecycle.reconcile_pending_operations(
        account_id=owner["account_id"], operation_id=prepared["operation_id"]
    )
    assert recovered["ok"] is False
    assert recovered["state"] == "pending_reconciliation"
    assert recovered["operations"][0]["state"] == "pending_reconciliation"
    blocked = lifecycle.prepare_trusted_phone_add(
        ctx=context(owner["account_id"], revision=2), idempotency_key="ambiguous-2"
    )
    assert blocked["state"] == "pending_reconciliation"
    assert blocked["ok"] is False
    assert "verified_success" not in str(recovered)


def test_account_unavailable_recovery_stays_pending_and_blocks_mutations(setup):
    registry, owner = setup
    prepared = lifecycle.prepare_trusted_phone_add(
        ctx=context(owner["account_id"]), idempotency_key="unavailable-1"
    )
    lifecycle.set_failure_point("before_registry_replacement")
    pending = lifecycle.apply_trusted_phone_operation(
        operation_id=prepared["operation_id"], account_id=owner["account_id"],
        action="trusted_phone_add", phone_e164="+13525550102", expected_auth_revision=0,
    )
    assert pending["state"] == "pending_reconciliation"
    lifecycle.set_failure_point(None)
    customers.upsert("+13525550100", status="churned")
    recovered = lifecycle.reconcile_pending_operations(
        account_id=owner["account_id"], operation_id=prepared["operation_id"]
    )
    assert recovered["ok"] is False
    assert recovered["state"] == "pending_reconciliation"
    blocked = lifecycle.prepare_trusted_phone_add(
        ctx=context(owner["account_id"]), idempotency_key="unavailable-2"
    )
    assert blocked["state"] == "pending_reconciliation"


def test_malformed_recovery_stays_pending_without_raw_payload_echo(setup):
    registry, owner = setup
    prepared = lifecycle.prepare_trusted_phone_add(
        ctx=context(owner["account_id"]), idempotency_key="malformed-1"
    )
    lifecycle.set_failure_point("before_registry_replacement")
    pending = lifecycle.apply_trusted_phone_operation(
        operation_id=prepared["operation_id"], account_id=owner["account_id"],
        action="trusted_phone_add", phone_e164="+13525550102", expected_auth_revision=0,
    )
    assert pending["state"] == "pending_reconciliation"
    lifecycle.set_failure_point(None)
    with lifecycle._connect(lifecycle._db_path()) as conn:
        conn.execute(
            "UPDATE operations SET payload_json = ? WHERE operation_id = ?",
            ("{malformed", prepared["operation_id"]),
        )
    recovered = lifecycle.reconcile_pending_operations(
        account_id=owner["account_id"], operation_id=prepared["operation_id"]
    )
    assert recovered["ok"] is False
    assert recovered["state"] == "pending_reconciliation"
    assert "13525550102" not in str(recovered)
