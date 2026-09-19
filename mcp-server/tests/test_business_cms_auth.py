"""CMS owner authority adapter."""

from __future__ import annotations

import importlib
import time

import pytest

import business_cms_auth as auth
import customers


@pytest.fixture
def owner_reg(tmp_path, monkeypatch):
    path = tmp_path / "customers.json"
    path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("CUSTOMERS_PATH", str(path))
    monkeypatch.setenv("BUSINESS_CMS_SESSION_KEY", "cms-test-key-cms-test-key-cms-test")
    importlib.reload(customers)
    importlib.reload(auth)
    customers.upsert("+13555550100", status="active_owner", slug="cool-cafe", business_name="Cool Cafe")
    yield path
    importlib.reload(customers)
    importlib.reload(auth)


def test_issue_and_require_owner(owner_reg):
    token = auth.issue_owner_session("cool-cafe", account_id="acct-1", actor="owner-1")
    principal = auth.require_owner("cool-cafe", token, "cms_publish")
    assert principal.slug == "cool-cafe"
    assert principal.account_id == "acct-1"


def test_wrong_tenant_and_wrong_purpose(owner_reg):
    token = auth.issue_owner_session(
        "cool-cafe",
        account_id="acct-1",
        actor="owner-1",
        actions={"cms_draft"},
    )
    with pytest.raises(auth.CmsAuthError) as wrong:
        auth.require_owner("other-shop", token, "cms_draft")
    assert wrong.value.code in {"wrong_tenant", "invalid_session"}
    with pytest.raises(auth.CmsAuthError) as purpose:
        auth.require_owner("cool-cafe", token, "cms_publish")
    assert purpose.value.code == "wrong_purpose"


def test_expired_session(owner_reg, monkeypatch):
    token = auth.issue_owner_session("cool-cafe", account_id="acct-1", actor="owner-1", ttl=60)
    frozen = time.time() + 10_000
    monkeypatch.setattr(auth.time, "time", lambda: frozen)
    with pytest.raises(auth.CmsAuthError) as expired:
        auth.require_owner("cool-cafe", token, "cms_draft")
    assert expired.value.code == "invalid_session"


def test_revoked_eligibility(owner_reg):
    token = auth.issue_owner_session("cool-cafe", account_id="acct-1", actor="owner-1")
    customers.upsert("+13555550100", status="churned", slug="cool-cafe")
    with pytest.raises(auth.CmsAuthError) as revoked:
        auth.require_owner("cool-cafe", token, "cms_draft")
    assert revoked.value.code == "not_active_owner"


def test_non_owner_cannot_mint(owner_reg):
    customers.upsert("+13555550999", status="callback_queued", slug="prospect-shop")
    with pytest.raises(auth.CmsAuthError) as exc:
        auth.issue_owner_session("prospect-shop", account_id="acct-2", actor="x")
    assert exc.value.code == "not_active_owner"


def test_missing_key_fail_closed(tmp_path, monkeypatch):
    path = tmp_path / "customers.json"
    path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("CUSTOMERS_PATH", str(path))
    monkeypatch.delenv("BUSINESS_CMS_SESSION_KEY", raising=False)
    monkeypatch.delenv("ACCOUNT_LIFECYCLE_SERVICE_KEY", raising=False)
    importlib.reload(customers)
    importlib.reload(auth)
    customers.upsert("+13555550100", status="active_owner", slug="cool-cafe")
    with pytest.raises(auth.CmsAuthError) as exc:
        auth.issue_owner_session("cool-cafe", account_id="acct-1", actor="o")
    assert exc.value.code == "key_unset"
