"""Focused tests for the default-off SQLite lifecycle store."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import account_lifecycle as lifecycle


@pytest.fixture
def store(tmp_path, monkeypatch):
    db = tmp_path / "account-lifecycle.sqlite3"
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_DB", str(db))
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_ENABLED", "true")
    lifecycle._init_schema(db)
    return db


def context(*, action: str, account_id: str = "acct-1", auth_revision: int = 7):
    return lifecycle.LifecycleContext(
        session_id="session-1",
        account_id=account_id,
        caller_transport_binding="call-1",
        auth_revision=auth_revision,
        action=action,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        capability_id="capability-1",
        account_status="paid",
        owner_authenticated=True,
        proof_fresh=True,
    )


def test_schema_has_required_tables_indexes_and_constraints(store):
    with lifecycle._connect(store) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"client_pages", "operations", "challenges", "audit_events"} <= tables
        index_names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert "idx_operations_account" in index_names
        assert "idx_audit_operation" in index_names


def test_lifecycle_writes_are_denied_by_default(store, monkeypatch):
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_ENABLED", "false")

    result = lifecycle.prepare_client_page_create(
        ctx=context(action="client_page_create"),
        title="A page",
        body="A useful description.",
        idempotency_key="request-1",
    )

    assert result["state"] == "denied"
    assert result["code"] == "feature_disabled"
    assert "operation_id" not in result


def test_page_create_prepare_is_idempotent_and_audited(store):
    ctx = context(action="client_page_create")
    first = lifecycle.prepare_client_page_create(
        ctx=ctx,
        title="A page",
        body="A useful description.",
        idempotency_key="request-1",
    )
    again = lifecycle.prepare_client_page_create(
        ctx=ctx,
        title="A page",
        body="A useful description.",
        idempotency_key="request-1",
    )

    assert first["state"] == "awaiting_confirmation"
    assert first["operation_id"] == again["operation_id"]
    assert first["payload_digest"] == again["payload_digest"]
    assert "A useful description" not in json.dumps(first)

    with lifecycle._connect(store) as conn:
        audit = conn.execute(
            "SELECT event_type, payload_digest, metadata_json FROM audit_events"
        ).fetchall()
    assert len(audit) == 1
    assert audit[0][0] == "prepared"
    assert first["payload_digest"] in audit[0][1]
    assert "A useful description" not in audit[0][2]


def test_same_idempotency_key_with_changed_payload_fails_closed(store):
    ctx = context(action="client_page_create")
    first = lifecycle.prepare_client_page_create(
        ctx=ctx, title="A page", body="one", idempotency_key="request-1"
    )
    conflict = lifecycle.prepare_client_page_create(
        ctx=ctx, title="A page", body="two", idempotency_key="request-1"
    )

    assert first["state"] == "awaiting_confirmation"
    assert conflict["state"] == "denied"
    assert conflict["code"] == "idempotency_conflict"
    assert "two" not in json.dumps(conflict)


def test_create_commit_is_atomic_idempotent_and_public_read_is_no_store(store):
    ctx = context(action="client_page_create")
    prepared = lifecycle.prepare_client_page_create(
        ctx=ctx,
        title="A page",
        body="A useful description.",
        idempotency_key="request-1",
    )
    token = "server-keypad-token"
    lifecycle.bind_operation_confirmation(
        ctx=ctx,
        operation_id=prepared["operation_id"],
        confirmation_digest=hashlib.sha256(token.encode()).hexdigest(),
    )

    committed = lifecycle.commit_account_operation(
        ctx=ctx,
        operation_id=prepared["operation_id"],
        confirmation_token=token,
    )
    retried = lifecycle.commit_account_operation(
        ctx=ctx,
        operation_id=prepared["operation_id"],
        confirmation_token=token,
    )

    assert committed["state"] == "verified_success"
    assert committed["page_id"] == retried["page_id"]
    assert committed["slug"].startswith("p-")
    status, payload, cache_control = lifecycle.get_public_page(committed["slug"])
    assert status == 200
    assert cache_control == "no-store"
    assert "A useful description." in payload["html"]
    assert "acct-1" not in payload["html"]


def test_remove_tombstones_page_and_reserves_slug_with_410(store):
    create_ctx = context(action="client_page_create")
    prepared = lifecycle.prepare_client_page_create(
        ctx=create_ctx,
        title="A page",
        body="A useful description.",
        idempotency_key="create-1",
    )
    token = "create-token"
    lifecycle.bind_operation_confirmation(
        ctx=create_ctx,
        operation_id=prepared["operation_id"],
        confirmation_digest=hashlib.sha256(token.encode()).hexdigest(),
    )
    created = lifecycle.commit_account_operation(
        ctx=create_ctx,
        operation_id=prepared["operation_id"],
        confirmation_token=token,
    )

    remove_ctx = context(action="client_page_remove")
    removal = lifecycle.prepare_client_page_remove(
        ctx=remove_ctx,
        page_id=created["page_id"],
        idempotency_key="remove-1",
    )
    remove_token = "remove-token"
    lifecycle.bind_operation_confirmation(
        ctx=remove_ctx,
        operation_id=removal["operation_id"],
        confirmation_digest=hashlib.sha256(remove_token.encode()).hexdigest(),
    )
    removed = lifecycle.commit_account_operation(
        ctx=remove_ctx,
        operation_id=removal["operation_id"],
        confirmation_token=remove_token,
    )

    assert removed["state"] == "verified_success"
    status, payload, cache_control = lifecycle.get_public_page(created["slug"])
    assert status == 410
    assert payload == {}
    assert cache_control == "no-store"
    assert lifecycle.verify_client_page_publication(created["page_id"], "tombstoned")["ok"] is True


def test_public_reads_distinguish_unknown_and_unpublished(store):
    assert lifecycle.get_public_page("p-does-not-exist") == (404, {}, "no-store")
    with lifecycle._connect(store) as conn:
        conn.execute(
            """INSERT INTO client_pages
               (page_id, account_id, slug, title, body, payload_digest, state,
                created_at, page_version)
               VALUES ('page-hidden', 'acct-1', 'p-hidden', 'Hidden', 'body', 'digest',
                       'draft', '2026-01-01T00:00:00+00:00', 1)"""
        )
    assert lifecycle.get_public_page("p-hidden") == (404, {}, "no-store")


def test_context_and_page_ownership_fail_closed(store):
    invalid = lifecycle.prepare_client_page_create(
        ctx={"account_id": "acct-1"},
        title="A page",
        body="body",
        idempotency_key="request-1",
    )
    assert invalid["state"] == "denied"
    assert invalid["code"] == "invalid_context"

    prepared = lifecycle.prepare_client_page_create(
        ctx=context(action="client_page_create"),
        title="A page",
        body="body",
        idempotency_key="request-2",
    )
    other = lifecycle.prepare_client_page_remove(
        ctx=context(action="client_page_remove", account_id="acct-2"),
        page_id=prepared["page_id"],
        idempotency_key="remove-1",
    )
    assert other["state"] == "denied"
    assert other["code"] == "not_found"


def _confirm(ctx, prepared, token):
    lifecycle.bind_operation_confirmation(
        ctx=ctx,
        operation_id=prepared["operation_id"],
        confirmation_digest=hashlib.sha256(token.encode()).hexdigest(),
    )
    return lifecycle.commit_account_operation(
        ctx=ctx,
        operation_id=prepared["operation_id"],
        confirmation_token=token,
    )


def test_removal_retry_checks_idempotency_before_published_state(store):
    create_ctx = context(action="client_page_create")
    created = _confirm(
        create_ctx,
        lifecycle.prepare_client_page_create(
            ctx=create_ctx, title="A page", body="body", idempotency_key="create-1"
        ),
        "create-token",
    )
    remove_ctx = context(action="client_page_remove")
    prepared = lifecycle.prepare_client_page_remove(
        ctx=remove_ctx, page_id=created["page_id"], idempotency_key="remove-1"
    )
    removed = _confirm(remove_ctx, prepared, "remove-token")
    retried_prepare = lifecycle.prepare_client_page_remove(
        ctx=remove_ctx, page_id=created["page_id"], idempotency_key="remove-1"
    )

    assert removed["state"] == "verified_success"
    assert retried_prepare == removed


def test_expiry_persists_a_stable_receipt_for_retries(store):
    ctx = context(action="client_page_create")
    prepared = lifecycle.prepare_client_page_create(
        ctx=ctx, title="A page", body="body", idempotency_key="expire-1"
    )
    with lifecycle._connect(store) as conn:
        conn.execute(
            "UPDATE operations SET expires_at = ? WHERE operation_id = ?",
            ("2020-01-01T00:00:00+00:00", prepared["operation_id"]),
        )

    first = lifecycle.commit_account_operation(
        ctx=ctx, operation_id=prepared["operation_id"], confirmation_token="unused"
    )
    second = lifecycle.commit_account_operation(
        ctx=ctx, operation_id=prepared["operation_id"], confirmation_token="different"
    )

    assert first == second
    assert first["state"] == "failed"
    assert first["code"] == "expired"
    with lifecycle._connect(store) as conn:
        receipt = conn.execute(
            "SELECT state, receipt_json FROM operations WHERE operation_id = ?",
            (prepared["operation_id"],),
        ).fetchone()
    assert receipt["state"] == "failed"
    assert json.loads(receipt["receipt_json"])["code"] == "expired"


def test_corrupt_sqlite_returns_safe_failed_result(store, monkeypatch):
    def raise_corrupt():
        raise sqlite3.DatabaseError("file is not a database")

    monkeypatch.setattr(lifecycle, "_open_store", raise_corrupt)
    result = lifecycle.prepare_trusted_phone_add(
        ctx=context(action="trusted_phone_add"), idempotency_key="storage-1"
    )

    assert result == {"ok": False, "state": "failed", "code": "storage_unavailable"}


def test_reserved_slug_cannot_be_reused_after_tombstone(store, monkeypatch):
    monkeypatch.setattr(lifecycle.secrets, "token_hex", lambda _: "reserved")
    create_ctx = context(action="client_page_create")
    created = _confirm(
        create_ctx,
        lifecycle.prepare_client_page_create(
            ctx=create_ctx, title="A page", body="body", idempotency_key="create-1"
        ),
        "create-token",
    )
    remove_ctx = context(action="client_page_remove")
    removal = lifecycle.prepare_client_page_remove(
        ctx=remove_ctx, page_id=created["page_id"], idempotency_key="remove-1"
    )
    _confirm(remove_ctx, removal, "remove-token")

    replacement = lifecycle.prepare_client_page_create(
        ctx=create_ctx, title="Another page", body="body", idempotency_key="create-2"
    )
    replacement_result = _confirm(create_ctx, replacement, "replacement-token")

    assert replacement_result["state"] == "failed"
    assert lifecycle.get_public_page(created["slug"])[0] == 410


def test_only_typed_auth_context_with_action_capability_can_mutate(store):
    forged_mapping = {
        "session_id": "session-1",
        "account_id": "acct-1",
        "caller_transport_binding": "call-1",
        "auth_revision": 7,
        "action": "client_page_create",
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
        "capability_id": "capability-1",
        "account_status": "paid",
        "owner_authenticated": True,
        "proof_fresh": True,
    }
    forged_object = type("ForgedContext", (), forged_mapping)()

    for forged in (forged_mapping, forged_object):
        result = lifecycle.prepare_client_page_create(
            ctx=forged, title="A page", body="body", idempotency_key="forged-1"
        )
        assert result == {"ok": False, "state": "denied", "code": "invalid_context"}

    wrong_capability = context(action="client_page_remove")
    result = lifecycle.prepare_client_page_create(
        ctx=wrong_capability, title="A page", body="body", idempotency_key="wrong-action-1"
    )
    assert result == {"ok": False, "state": "denied", "code": "invalid_context"}


def test_digit_bearing_opaque_phone_ref_is_accepted(store):
    result = lifecycle.prepare_trusted_phone_remove(
        ctx=context(action="trusted_phone_remove"),
        phone_ref="phone_ref_42",
        idempotency_key="phone-remove-1",
    )

    assert result["state"] == "awaiting_confirmation"
