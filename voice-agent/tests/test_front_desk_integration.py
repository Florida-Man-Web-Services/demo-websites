"""Integrated front-desk session uses published CMS knowledge."""

from __future__ import annotations

import importlib
import json

import pytest

import agent
import mcp_bridge


@pytest.fixture
def published(tmp_path, monkeypatch):
    monkeypatch.setenv("BUSINESS_CMS_ENABLED", "true")
    monkeypatch.setenv("FRONT_DESK_ENABLED", "true")
    monkeypatch.setenv("BUSINESS_CMS_DATA_DIR", str(tmp_path / "cms"))
    import sys
    from pathlib import Path

    mcp = Path(__file__).resolve().parents[2] / "mcp-server"
    sys.path.insert(0, str(mcp))
    import business_cms_schema as schema
    import business_cms_store as store

    importlib.reload(store)
    doc = schema.empty_document("cool-cafe", "Cool Cafe")
    doc["identity"]["name"] = "Cool Cafe"
    saved = store.save_draft("cool-cafe", doc, expected_draft_rev=None, actor="o")
    store.publish(
        "cool-cafe",
        expected_draft_rev=saved["draft_revision"],
        expected_release_id=None,
        actor="o",
        html="<html><body>Cool Cafe</body></html>",
    )
    mcp_bridge.reset_for_tests()
    return saved


def test_bridge_get_business_and_message(published):
    raw = mcp_bridge.run_front_desk_tool(
        "front_desk_get_business",
        {"section": "identity"},
        slug="cool-cafe",
    )
    payload = json.loads(raw)
    assert payload["ok"] is True
    ident = payload["data"].get("identity") or payload["data"]
    assert ident.get("name") == "Cool Cafe"
    msg = json.loads(
        mcp_bridge.run_front_desk_tool(
            "front_desk_leave_message",
            {"message": "Need catering", "use_caller_callback": False},
            slug="cool-cafe",
            idempotency_key="k1",
        )
    )
    assert msg["ok"] is True
    assert "contact" not in msg


def test_paid_default_mode_is_still_owner_updates(monkeypatch, tmp_path):
    path = tmp_path / "customers.json"
    path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("CUSTOMERS_PATH", str(path))
    import customers

    importlib.reload(customers)
    customers.upsert("+13555550100", status="active_owner", slug="cool-cafe")
    mode = customers.resolve_mode("+13555550100", env_mode="auto")
    assert mode == "owner_updates"
    assert "front_desk" not in {t["name"] for t in agent.get_tools(mode)}
