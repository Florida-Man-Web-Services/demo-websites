"""Phase 1 owner auth_level gates (voice_auth + CallState)."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

AGENT_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = AGENT_DIR.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


def _reload_owner():
    os.environ["AGENT_MODE"] = "owner_updates"
    os.environ.pop("VOICE_AGENT_MODE", None)
    os.environ["VOICE_AUTH_VENDOR"] = "none"
    os.environ["VOICE_ENROLL_REQUIRED_FOR_WRITE"] = "false"

    import config as config_mod
    import agent as agent_mod
    import owner_updates as owner_mod
    import mcp_bridge as bridge_mod
    import voice_auth as va_mod

    importlib.reload(config_mod)
    importlib.reload(va_mod)
    importlib.reload(owner_mod)
    importlib.reload(bridge_mod)
    bridge_mod.reset_for_tests()
    importlib.reload(agent_mod)
    return config_mod, agent_mod, va_mod, bridge_mod


class _Biz:
    name = "Test Biz"
    category = "cafe"
    address = "1 Main St"
    rating = "4.5"
    demo_url = "https://example.com/test-biz.html"
    slug = "test-biz"


@pytest.fixture(autouse=True)
def _restore():
    yield
    os.environ["AGENT_MODE"] = "sales"
    import config, agent, owner_updates, mcp_bridge, voice_auth

    importlib.reload(config)
    importlib.reload(voice_auth)
    importlib.reload(owner_updates)
    importlib.reload(mcp_bridge)
    mcp_bridge.reset_for_tests()
    importlib.reload(agent)


def test_compute_initial_auth_legacy_unknown_phone(tmp_path, monkeypatch):
    monkeypatch.setenv("CUSTOMERS_PATH", str(tmp_path / "c.json"))
    (tmp_path / "c.json").write_text("{}\n", encoding="utf-8")
    _, _, va, _ = _reload_owner()
    snap = va.compute_initial_auth("+13555550100")
    assert snap["auth_level"] == "cid_legacy"


def test_compute_initial_auth_paid_owner(tmp_path, monkeypatch):
    path = tmp_path / "c.json"
    monkeypatch.setenv("CUSTOMERS_PATH", str(path))
    mcp = REPO_ROOT / "mcp-server"
    if str(mcp) not in sys.path:
        sys.path.insert(0, str(mcp))
    import customers

    importlib.reload(customers)
    customers.upsert("+13555550100", status="active_owner", slug="cool-cafe")
    _, _, va, _ = _reload_owner()
    snap = va.compute_initial_auth("+13555550100")
    assert snap["auth_level"] == "cid_only"


def test_anonymous_cannot_create_cr():
    _, agent, va, _ = _reload_owner()
    state = agent.CallState(
        call_sid="CA-AUTH",
        business=_Biz(),
        direction="inbound",
        caller_number="",  # no F1
    )
    state.llm = mock.Mock()
    state.auth_level = "anonymous"
    out = json.loads(agent._run_tool(state, "create_change_request", {
        "business_slug": "cool-cafe",
        "summary": "nope",
        "items": [],
    }))
    assert out.get("denied") is True or out.get("created") is False
    assert out.get("code") in ("auth_anonymous", "auth_insufficient", "not_owner")


def test_legacy_cid_can_create_unclaimed(tmp_path, monkeypatch):
    cr_path = tmp_path / "cr.jsonl"
    sites = tmp_path / "sites"
    sites.mkdir()
    (sites / "cool-cafe.html").write_text(
        "<html><head><title>Cool</title></head><body><h1>Cool</h1></body></html>",
        encoding="utf-8",
    )
    monkeypatch.setenv("CHANGE_REQUESTS_PATH", str(cr_path))
    monkeypatch.setenv("GENERATED_SITES_DIR", str(sites))
    monkeypatch.setenv("CUSTOMERS_PATH", str(tmp_path / "empty-cust.json"))
    (tmp_path / "empty-cust.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("OWNER_CR_AUTH", "soft")

    for key in list(sys.modules):
        if key in ("changerequests", "customers", "lookup", "siteedit", "mcp_bridge"):
            del sys.modules[key]

    _, agent, va, bridge = _reload_owner()
    bridge.reset_for_tests()
    state = agent.CallState(
        call_sid="CA-LEG",
        business=_Biz(),
        direction="inbound",
        caller_number="+13555550100",
    )
    state.llm = mock.Mock()
    va.apply_auth_to_state(state)
    assert state.auth_level == "cid_legacy"

    created = json.loads(
        agent._run_tool(
            state,
            "create_change_request",
            {
                "business_slug": "cool-cafe",
                "summary": "hours",
                "items": [{"type": "hours", "after": "9-5"}],
                "confirmation_spoken": True,
            },
        )
    )
    assert created.get("created") is True, created


def test_check_tool_allowed_voice_soft_pending(monkeypatch):
    monkeypatch.setenv("VOICE_AUTH_VENDOR", "mock")
    monkeypatch.setenv("VOICE_ENROLL_REQUIRED_FOR_WRITE", "false")
    _, agent, va, _ = _reload_owner()
    # Force nominal voice_soft requirement
    monkeypatch.setenv("VOICE_AUTH_VENDOR", "mock")
    importlib.reload(va)
    state = agent.CallState(
        call_sid="CA-V",
        business=_Biz(),
        direction="inbound",
        caller_number="+13555550100",
        auth_level="cid_only",
    )
    # With mock vendor, create needs voice_soft
    deny = va.check_tool_allowed(state, "create_change_request")
    assert deny is not None
    assert deny.get("code") == "auth_voice_pending"

    # Promote via mock windows
    for _ in range(3):
        va.on_speech_window(state)
    assert state.auth_level in ("voice_soft", "voice_hard")
    assert va.check_tool_allowed(state, "create_change_request") is None


def test_read_tools_allowed_at_cid_only():
    _, agent, va, _ = _reload_owner()
    state = agent.CallState(
        call_sid="CA-R",
        business=_Biz(),
        direction="inbound",
        caller_number="+13555550100",
        auth_level="cid_only",
    )
    assert va.check_tool_allowed(state, "get_site_outline") is None
    assert va.check_tool_allowed(state, "lookup_business") is None
    assert va.check_tool_allowed(state, "end_call") is None
