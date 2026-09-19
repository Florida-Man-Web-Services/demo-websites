"""Inbox isolation and idempotency."""

from __future__ import annotations

import importlib

import pytest

import business_cms_schema as schema
import business_cms_store as store
import business_inbox as inbox


@pytest.fixture
def ready(tmp_path, monkeypatch):
    monkeypatch.setenv("BUSINESS_CMS_ENABLED", "true")
    monkeypatch.setenv("BUSINESS_CMS_DATA_DIR", str(tmp_path / "cms"))
    importlib.reload(store)
    importlib.reload(inbox)
    for slug in ("cool-cafe", "other-shop"):
        doc = schema.empty_document(slug, slug)
        saved = store.save_draft(slug, doc, expected_draft_rev=None, actor="o")
        store.publish(
            slug,
            expected_draft_rev=saved["draft_revision"],
            expected_release_id=None,
            actor="o",
            html=f"<html><body>{slug}</body></html>",
        )
    return tmp_path


def test_cross_tenant_inbox_isolation(ready):
    inbox.create_request("cool-cafe", kind="message", message="one", source="web", idempotency_key="a")
    inbox.create_request("other-shop", kind="message", message="two", source="web", idempotency_key="b")
    cafe = inbox.list_requests("cool-cafe")
    other = inbox.list_requests("other-shop")
    assert [row["message"] for row in cafe] == ["one"]
    assert [row["message"] for row in other] == ["two"]


def test_disabled_inbox(tmp_path, monkeypatch):
    monkeypatch.setenv("BUSINESS_CMS_ENABLED", "false")
    monkeypatch.setenv("BUSINESS_CMS_DATA_DIR", str(tmp_path / "cms"))
    importlib.reload(store)
    importlib.reload(inbox)
    with pytest.raises(inbox.InboxError) as exc:
        inbox.create_request("cool-cafe", kind="message", message="x", source="web")
    assert exc.value.code == "feature_disabled"
