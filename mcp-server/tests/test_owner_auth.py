"""Phase 0 owner F1 auth: trusted phones + CR ownership gates."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest

import customers as cust
import changerequests as cr


@pytest.fixture
def customer_store(tmp_path, monkeypatch):
    path = tmp_path / "customers.json"
    path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("CUSTOMERS_PATH", str(path))
    monkeypatch.setenv("OWNER_CR_AUTH", "soft")
    # Drop module path cache if any
    importlib.reload(cust)
    yield path
    importlib.reload(cust)


@pytest.fixture
def cr_store(tmp_path, monkeypatch):
    path = tmp_path / "change-requests.jsonl"
    monkeypatch.setenv("CHANGE_REQUESTS_PATH", str(path))
    sites = tmp_path / "sites"
    sites.mkdir()
    (sites / "cool-cafe.html").write_text(
        "<html><head><title>Cool Cafe</title></head><body><h1>Cool Cafe</h1></body></html>",
        encoding="utf-8",
    )
    monkeypatch.setenv("GENERATED_SITES_DIR", str(sites))
    importlib.reload(cr)
    yield path
    importlib.reload(cr)


def test_trusted_phones_include_primary_and_extras(customer_store):
    r = cust.upsert(
        "+13555550100",
        status="active_owner",
        slug="cool-cafe",
        business_name="Cool Cafe",
        patch={"trusted_phones": ["(352) 555-0199", "+13555550100"]},
    )
    assert r["ok"]
    phones = cust.trusted_phones_for(r["customer"])
    assert "+13555550100" in phones
    # (352) 555-0199 → +13525550199
    assert "+13525550199" in phones


def test_authorize_owner_ok_for_paid_owner(customer_store):
    cust.upsert(
        "+13555550100",
        status="active_owner",
        slug="cool-cafe",
    )
    auth = cust.authorize_owner_write("+13555550100", "cool-cafe")
    assert auth["ok"] is True
    assert auth["auth_level"] == "cid_only"


def test_authorize_denies_wrong_phone_on_claimed_slug(customer_store):
    cust.upsert("+13555550100", status="active_owner", slug="cool-cafe")
    auth = cust.authorize_owner_write("+13555550999", "cool-cafe")
    assert auth["ok"] is False
    assert auth["code"] == "not_owner"


def test_authorize_denies_non_paid_status(customer_store):
    cust.upsert("+13555550100", status="callback_queued", slug="cool-cafe")
    auth = cust.authorize_owner_write("+13555550100", "cool-cafe")
    assert auth["ok"] is False
    assert auth["code"] == "not_active_owner"


def test_authorize_soft_allows_unclaimed_legacy(customer_store, monkeypatch):
    monkeypatch.setenv("OWNER_CR_AUTH", "soft")
    importlib.reload(cust)
    auth = cust.authorize_owner_write("+13555550100", "no-one-owns-this")
    assert auth["ok"] is True
    assert auth["auth_level"] == "cid_legacy"


def test_authorize_strict_requires_registry(customer_store, monkeypatch):
    monkeypatch.setenv("OWNER_CR_AUTH", "strict")
    importlib.reload(cust)
    auth = cust.authorize_owner_write("+13555550100", "no-one-owns-this")
    assert auth["ok"] is False
    assert auth["code"] == "not_registered"


def test_create_cr_denied_for_wrong_cid(customer_store, cr_store):
    cust.upsert("+13555550100", status="active_owner", slug="cool-cafe")
    bad = cr.create_change_request(
        "cool-cafe",
        "Steal hours",
        items=[{"type": "hours", "after": "never"}],
        caller_phone="+13555550999",
    )
    assert bad.get("created") is False
    assert bad.get("code") == "not_owner"

    good = cr.create_change_request(
        "cool-cafe",
        "Update hours",
        items=[{"type": "hours", "after": "9-5"}],
        caller_phone="+13555550100",
    )
    assert good.get("created") is True


def test_create_cr_legacy_unclaimed_still_works(customer_store, cr_store):
    """Existing unit tests / demos without registry keep working under soft auth."""
    out = cr.create_change_request(
        "cool-cafe",
        "Legacy demo update",
        items=[{"type": "copy", "after": "hi"}],
        caller_phone="+13555550100",
    )
    assert out.get("created") is True


def test_delegate_trusted_phone_can_write(customer_store, cr_store):
    cust.upsert(
        "+13555550100",
        status="active_owner",
        slug="cool-cafe",
        patch={
            "trusted_phones": ["+13555550100"],
            "delegates": [{"phone": "+13555550888", "name": "Mgr"}],
        },
    )
    out = cr.create_change_request(
        "cool-cafe",
        "Manager hours tweak",
        items=[{"type": "hours", "after": "10-4"}],
        caller_phone="+13555550888",
    )
    assert out.get("created") is True


def test_cancel_denied_for_stranger(customer_store, cr_store, monkeypatch):
    monkeypatch.setenv("OWNER_CR_AUTH", "off")
    importlib.reload(cust)
    importlib.reload(cr)
    created = cr.create_change_request(
        "cool-cafe",
        "temp",
        items=[],
        caller_phone="+13555550100",
    )
    assert created["created"]
    monkeypatch.setenv("OWNER_CR_AUTH", "soft")
    importlib.reload(cust)
    importlib.reload(cr)
    cust.upsert("+13555550100", status="active_owner", slug="cool-cafe")
    denied = cr.cancel_change_request(created["id"], caller_phone="+13555550999")
    assert denied.get("cancelled") is False
    assert denied.get("code") == "not_owner"


def test_mark_voice_enrolled_requires_paid(customer_store):
    cust.upsert("+13555550100", status="callback_queued", slug="cool-cafe")
    bad = cust.mark_voice_enrolled("+13555550100")
    assert bad.get("ok") is False
    cust.upsert("+13555550100", status="active_owner", slug="cool-cafe")
    good = cust.mark_voice_enrolled("+13555550100", vendor="mock")
    assert good.get("ok") is True
    va = good["customer"]["voice_auth"]
    assert va.get("enrolled_at")
    assert va.get("template_id")
    assert va.get("consented_at")
    cleared = cust.clear_voice_auth("+13555550100")
    assert cleared.get("ok") is True
    assert not (cleared["customer"].get("voice_auth") or {}).get("template_id")


def test_touch_and_verify_streak(customer_store):
    cust.upsert("+13555550100", status="active_owner", slug="cool-cafe")
    cust.mark_voice_enrolled("+13555550100", vendor="mock")
    t = cust.touch_owner_call("+13555550100", ani="+13555550100")
    assert t["ok"]
    assert t["customer"]["voice_auth"].get("last_call_at")
    assert t["customer"]["voice_auth"].get("last_ani") == "+13555550100"
    f1 = cust.record_voice_verify_result("+13555550100", ok=False)
    assert f1["customer"]["voice_auth"]["fail_streak"] == 1
    f2 = cust.record_voice_verify_result("+13555550100", ok=False)
    assert f2["customer"]["voice_auth"]["fail_streak"] == 2
    ok = cust.record_voice_verify_result("+13555550100", ok=True, score=0.9)
    assert ok["customer"]["voice_auth"]["fail_streak"] == 0
    assert ok["customer"]["voice_auth"].get("last_verify_at")

