"""Owner CMS HTTP + public page isolation."""

from __future__ import annotations

import importlib
import json

import pytest

import business_cms_auth as auth
import business_cms_http as http
import business_cms_schema as schema
import business_cms_store as store
import customers


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BUSINESS_CMS_ENABLED", "true")
    monkeypatch.setenv("BUSINESS_CMS_DATA_DIR", str(tmp_path / "cms"))
    monkeypatch.setenv("BUSINESS_CMS_SESSION_KEY", "cms-test-key-cms-test-key-cms-test")
    monkeypatch.setenv("BUSINESS_CMS_PUBLIC_ORIGIN", "https://example.test")
    path = tmp_path / "customers.json"
    path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("CUSTOMERS_PATH", str(path))
    importlib.reload(customers)
    importlib.reload(auth)
    importlib.reload(store)
    importlib.reload(http)
    customers.upsert("+13555550100", status="active_owner", slug="cool-cafe")
    return tmp_path


def _cookies(resp):
    return "; ".join(f"{k}={v}" for k, v in resp.cookies.items() if v)


def _session(env):
    resp = http.dispatch(
        "POST",
        "/api/business-cms/cool-cafe/session",
        headers={"Content-Type": "application/json"},
        body=json.dumps({"account_id": "acct-1", "actor": "owner-1"}),
    )
    assert resp.status == 200, resp.body
    return resp


def test_unauthenticated_owner_denied(env):
    resp = http.dispatch("GET", "/cms/businesses/cool-cafe/")
    assert resp.status == 401
    pub = http.dispatch("GET", "/businesses/cool-cafe/")
    assert pub.status == 404
    assert pub.body == b""


def test_csrf_and_cross_tenant(env):
    session = _session(env)
    cookie = _cookies(session)
    csrf = session.cookies[http.CSRF_COOKIE]
    doc = schema.empty_document("cool-cafe", "Cafe")
    denied = http.dispatch(
        "PUT",
        "/api/business-cms/cool-cafe/draft",
        headers={"Content-Type": "application/json", "Cookie": cookie},
        body=json.dumps({"expected_draft_rev": None, "document": doc}),
    )
    assert denied.status == 403
    ok = http.dispatch(
        "PUT",
        "/api/business-cms/cool-cafe/draft",
        headers={"Content-Type": "application/json", "Cookie": cookie, "X-CSRF-Token": csrf},
        body=json.dumps({"expected_draft_rev": None, "document": doc}),
    )
    assert ok.status == 200
    other = http.dispatch(
        "GET",
        "/api/business-cms/other-shop/draft",
        headers={"Cookie": cookie, "X-CSRF-Token": csrf},
    )
    assert other.status == 401


def test_draft_not_public_until_publish(env):
    session = _session(env)
    cookie = _cookies(session)
    csrf = session.cookies[http.CSRF_COOKIE]
    doc = schema.empty_document("cool-cafe", "Secret Draft Name")
    doc["identity"]["name"] = "Secret Draft Name"
    saved = http.dispatch(
        "PUT",
        "/api/business-cms/cool-cafe/draft",
        headers={"Content-Type": "application/json", "Cookie": cookie, "X-CSRF-Token": csrf},
        body=json.dumps({"expected_draft_rev": None, "document": doc}),
    )
    assert saved.status == 200
    public = http.dispatch("GET", "/businesses/cool-cafe/")
    assert public.status == 404
    assert b"Secret Draft Name" not in public.body
    preview = http.dispatch(
        "POST",
        "/api/business-cms/cool-cafe/preview",
        headers={"Cookie": cookie, "X-CSRF-Token": csrf},
    )
    assert preview.status == 200
    assert b"Preview" in preview.body
    assert b"Secret Draft Name" in preview.body
    published = http.dispatch(
        "POST",
        "/api/business-cms/cool-cafe/publish",
        headers={"Content-Type": "application/json", "Cookie": cookie, "X-CSRF-Token": csrf},
        body="{}",
    )
    assert published.status == 200, published.body
    live = http.dispatch("GET", "/businesses/cool-cafe/")
    assert live.status == 200
    assert b"Secret Draft Name" in live.body
    assert live.headers.get("ETag")


def test_public_form_cannot_mutate_cms(env):
    session = _session(env)
    cookie = _cookies(session)
    csrf = session.cookies[http.CSRF_COOKIE]
    doc = schema.empty_document("cool-cafe", "Cafe")
    http.dispatch(
        "PUT",
        "/api/business-cms/cool-cafe/draft",
        headers={"Content-Type": "application/json", "Cookie": cookie, "X-CSRF-Token": csrf},
        body=json.dumps({"expected_draft_rev": None, "document": doc}),
    )
    http.dispatch(
        "POST",
        "/api/business-cms/cool-cafe/publish",
        headers={"Content-Type": "application/json", "Cookie": cookie, "X-CSRF-Token": csrf},
        body="{}",
    )
    posted = http.dispatch(
        "POST",
        "/businesses/cool-cafe/requests",
        headers={"Content-Type": "application/json"},
        body=json.dumps({"kind": "contact", "message": "hello"}),
    )
    assert posted.status == 200
    live = http.dispatch("GET", "/businesses/cool-cafe/")
    assert b"hello" not in live.body
    inbox = http.dispatch(
        "GET",
        "/api/business-cms/cool-cafe/inbox",
        headers={"Cookie": cookie, "X-CSRF-Token": csrf},
    )
    assert inbox.status == 200
    payload = json.loads(inbox.body)
    assert payload["requests"][0]["message"] == "hello"
