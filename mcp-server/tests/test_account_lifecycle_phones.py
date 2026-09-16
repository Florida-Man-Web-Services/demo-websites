"""Focused trusted-phone mutation tests."""

from datetime import datetime, timedelta, timezone
import importlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

import account_lifecycle as lifecycle
import customers
from account_verification import AuthContext


@pytest.fixture
def registry(tmp_path, monkeypatch):
    path = tmp_path / "customers.json"
    path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("CUSTOMERS_PATH", str(path))
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_DB", str(tmp_path / "lifecycle.sqlite3"))
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_ENABLED", "true")
    lifecycle.set_failure_point(None)
    importlib.reload(customers)
    lifecycle._init_schema(tmp_path / "lifecycle.sqlite3")
    yield path
    lifecycle.set_failure_point(None)
    importlib.reload(customers)


def ctx(account_id, revision=0, action="trusted_phone_add"):
    return AuthContext(
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
    )


def prepare(account_id, revision=0, key="op-1"):
    return lifecycle.prepare_trusted_phone_add(
        ctx=ctx(account_id, revision), idempotency_key=key
    )


def test_add_normalizes_globally_and_returns_one_durable_receipt(registry):
    primary = "+13525550100"
    destination = "+13525550102"
    owner = customers.upsert(primary, status="active_owner")["customer"]
    prepared = prepare(owner["account_id"])
    result = lifecycle.apply_trusted_phone_operation(
        operation_id=prepared["operation_id"],
        account_id=owner["account_id"],
        action="trusted_phone_add",
        phone_e164="(352) 555-0102",
        expected_auth_revision=0,
    )

    assert result["state"] == "verified_success"
    assert result["changed"] is True
    assert result["new_auth_revision"] == 1
    assert destination in customers.get(primary)["trusted_phones"]
    assert destination not in json.dumps(result)
    with lifecycle._connect(lifecycle._db_path()) as conn:
        rows = conn.execute(
            "SELECT event_type, metadata_json FROM audit_events WHERE operation_id = ?",
            (prepared["operation_id"],),
        ).fetchall()
        receipt = conn.execute(
            "SELECT receipt_json FROM operations WHERE operation_id = ?",
            (prepared["operation_id"],),
        ).fetchone()
    assert any(row[0] == "mutation_committed" for row in rows)
    assert destination not in json.dumps([dict(row) for row in rows])
    assert destination not in receipt[0]


def test_same_account_add_is_idempotent_without_revision_bump(registry):
    primary = "+13525550100"
    destination = "+13525550102"
    owner = customers.upsert(primary, status="active_owner", patch={"trusted_phones": [destination]})["customer"]
    prepared = prepare(owner["account_id"])
    result = lifecycle.apply_trusted_phone_operation(
        operation_id=prepared["operation_id"], account_id=owner["account_id"],
        action="trusted_phone_add", phone_e164=destination, expected_auth_revision=0,
    )
    assert result["state"] == "verified_noop"
    assert result["changed"] is False
    assert customers.get(primary)["auth_revision"] == 0


def test_concurrent_same_account_add_serializes_to_success_and_noop(registry):
    primary = "+13525550100"
    destination = "+13525550102"
    owner = customers.upsert(primary, status="active_owner")["customer"]
    first = prepare(owner["account_id"], key="concurrent-1")
    second = prepare(owner["account_id"], key="concurrent-2")

    def apply(prepared):
        return lifecycle.apply_trusted_phone_operation(
            operation_id=prepared["operation_id"], account_id=owner["account_id"],
            action="trusted_phone_add", phone_e164=destination, expected_auth_revision=0,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(apply, (first, second)))
    assert {result["state"] for result in results} == {"verified_success", "verified_noop"}
    assert customers.get(primary)["auth_revision"] == 1


def test_cross_account_collision_and_stale_revision_fail_closed(registry):
    primary = "+13525550100"
    collision = "+13525550102"
    first = customers.upsert(primary, status="active_owner")["customer"]
    second = customers.upsert("+13525550101", status="active_owner", patch={"trusted_phones": [collision]})["customer"]
    prepared = prepare(first["account_id"])
    collision_result = lifecycle.apply_trusted_phone_operation(
        operation_id=prepared["operation_id"], account_id=first["account_id"],
        action="trusted_phone_add", phone_e164=collision, expected_auth_revision=0,
    )
    assert collision_result["state"] == "failed"
    assert collision_result["code"] == "phone_collision"

    prepared = prepare(first["account_id"], key="op-2")
    customers.upsert(primary, patch={"auth_revision": 3})
    stale = lifecycle.apply_trusted_phone_operation(
        operation_id=prepared["operation_id"], account_id=first["account_id"],
        action="trusted_phone_add", phone_e164="+13525550103", expected_auth_revision=0,
    )
    assert stale["state"] == "failed"
    assert stale["code"] == "stale_auth_revision"


def test_remove_protects_primary_last_and_current_verification(registry):
    primary = "+13525550100"
    one = "+13525550102"
    two = "+13525550103"
    owner = customers.upsert(primary, status="active_owner", patch={"trusted_phones": [one, two]})["customer"]
    refs = lifecycle.lifecycle_phone_refs(owner["account_id"], ctx=ctx(owner["account_id"], action="lifecycle_phone_refs"))
    by_label = {item["label"].split()[0]: item for item in refs["phones"]}

    primary_op = lifecycle.prepare_trusted_phone_remove(
        ctx=ctx(owner["account_id"], action="trusted_phone_remove"),
        phone_ref=by_label["primary"]["phone_ref"], idempotency_key="rm-primary",
    )
    primary_result = lifecycle.apply_trusted_phone_operation(
        operation_id=primary_op["operation_id"], account_id=owner["account_id"],
        action="trusted_phone_remove", phone_e164="", expected_auth_revision=0,
    )
    assert primary_result["code"] == "primary_phone_protected"

    customers.upsert(primary, patch={"current_verification_phone": two})
    # The two trusted labels are deliberately located without revealing phones.
    trusted_refs = [item["phone_ref"] for item in refs["phones"] if not item["is_primary"]]
    current_op = lifecycle.prepare_trusted_phone_remove(
        ctx=ctx(owner["account_id"], action="trusted_phone_remove"),
        phone_ref=trusted_refs[1], idempotency_key="rm-current-2",
    )
    current_result = lifecycle.apply_trusted_phone_operation(
        operation_id=current_op["operation_id"], account_id=owner["account_id"],
        action="trusted_phone_remove", phone_e164="", expected_auth_revision=0,
    )
    assert current_result["code"] == "verification_phone_protected"

    last_phone = "+13525550104"
    last_trusted = "+13525550105"
    last_owner = customers.upsert(last_phone, status="active_owner", patch={"trusted_phones": [last_trusted]})["customer"]
    last_refs = lifecycle.lifecycle_phone_refs(last_owner["account_id"], ctx=ctx(last_owner["account_id"], action="lifecycle_phone_refs"))
    last_op = lifecycle.prepare_trusted_phone_remove(
        ctx=ctx(last_owner["account_id"], action="trusted_phone_remove"),
        phone_ref=[item for item in last_refs["phones"] if not item["is_primary"]][0]["phone_ref"],
        idempotency_key="rm-last",
    )
    last_result = lifecycle.apply_trusted_phone_operation(
        operation_id=last_op["operation_id"], account_id=last_owner["account_id"],
        action="trusted_phone_remove", phone_e164="", expected_auth_revision=0,
    )
    assert last_result["code"] == "last_trusted_phone_protected"


def test_successful_remove_invalidates_old_revision(registry):
    primary = "+13525550100"
    first = "+13525550102"
    second = "+13525550103"
    owner = customers.upsert(primary, status="active_owner", patch={"trusted_phones": [first, second]})["customer"]
    refs = lifecycle.lifecycle_phone_refs(owner["account_id"], ctx=ctx(owner["account_id"], action="lifecycle_phone_refs"))
    remove_ref = [item["phone_ref"] for item in refs["phones"] if not item["is_primary"]][0]
    prepared = lifecycle.prepare_trusted_phone_remove(
        ctx=ctx(owner["account_id"], action="trusted_phone_remove"),
        phone_ref=remove_ref, idempotency_key="remove-success",
    )
    result = lifecycle.apply_trusted_phone_operation(
        operation_id=prepared["operation_id"], account_id=owner["account_id"],
        action="trusted_phone_remove", phone_e164="", expected_auth_revision=0,
    )
    assert result["state"] == "verified_success"
    assert customers.get(primary)["auth_revision"] == 1
    assert first not in customers.get(primary)["trusted_phones"]
    assert lifecycle.prepare_trusted_phone_add(
        ctx=ctx(owner["account_id"], revision=0), idempotency_key="old-proof"
    )["code"] == "stale_auth_revision"
