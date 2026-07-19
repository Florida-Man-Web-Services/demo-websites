"""Unit tests for AGENT_MODE=owner_updates prompt/tool selection + CR intake (#52)."""

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


def _reload_mode(mode: str | None):
    """Reload config + agent modules under a given AGENT_MODE."""
    if mode is None:
        os.environ.pop("AGENT_MODE", None)
        os.environ.pop("VOICE_AGENT_MODE", None)
    else:
        os.environ["AGENT_MODE"] = mode
        os.environ.pop("VOICE_AGENT_MODE", None)

    import config as config_mod
    import agent as agent_mod
    import ai411 as ai411_mod
    import owner_updates as owner_mod
    import mcp_bridge as bridge_mod

    importlib.reload(config_mod)
    importlib.reload(ai411_mod)
    importlib.reload(owner_mod)
    importlib.reload(bridge_mod)
    bridge_mod.reset_for_tests()
    importlib.reload(agent_mod)
    return config_mod, agent_mod, owner_mod, bridge_mod


class _Biz:
    name = "Test Biz"
    category = "cafe"
    address = "1 Main St"
    rating = "4.5"
    demo_url = "https://example.com/test-biz.html"
    slug = "test-biz"


def _state(agent_mod, phone: str = "+13555550100"):
    state = agent_mod.CallState(
        call_sid="TEST-CR",
        business=_Biz(),
        direction="inbound",
        caller_number=phone,
    )
    state.llm = mock.Mock()
    return state


@pytest.fixture(autouse=True)
def _restore_sales_mode():
    yield
    _reload_mode("sales")


def test_default_mode_is_sales():
    config, agent, _, _ = _reload_mode(None)
    assert config.AGENT_MODE == "sales"
    assert config.is_ai411() is False
    assert config.is_owner_updates() is False
    prompt = agent.system_prompt(_Biz(), "inbound", "+13555550100")
    assert "$999" in prompt
    assert "Gainesville AI 411" not in prompt
    assert "owner site-updates" not in prompt.lower()
    names = {t["name"] for t in agent.get_tools()}
    assert names == {"send_demo_link_sms", "log_call_outcome", "end_call"}


def test_owner_updates_mode_prompt_and_tools():
    config, agent, owner, _ = _reload_mode("owner_updates")
    assert config.AGENT_MODE == "owner_updates"
    assert config.is_owner_updates() is True
    assert config.is_ai411() is False
    prompt = agent.system_prompt(_Biz(), "inbound", "+13555550100")
    # Change-desk language present
    assert "change" in prompt.lower() or "ChangeRequest" in prompt or "site-updates" in prompt
    assert "owner" in prompt.lower()
    # Not sales pricing, not AI 411 product greeting as primary
    assert "$999" not in prompt
    assert "Gainesville AI 411" not in prompt
    assert "selling websites" not in prompt
    # Weak auth + outline flow
    assert "phone" in prompt.lower()
    assert "get_site_outline" in prompt or "outline" in prompt.lower()
    assert "create_change_request" in prompt
    assert "911" in prompt

    names = {t["name"] for t in agent.get_tools()}
    expected = {
        "lookup_business",
        "get_site_outline",
        "create_change_request",
        "list_open_change_requests",
        "cancel_change_request",
        "apply_change_request",
        "send_sms_links",
        "end_call",
    }
    assert names == expected
    assert agent.get_openers() == owner.OPENERS


def test_ai411_still_isolated_from_owner():
    config, agent, _, _ = _reload_mode("ai411")
    assert config.is_ai411() is True
    assert config.is_owner_updates() is False
    prompt = agent.system_prompt(_Biz(), "inbound", "+13555550100")
    assert "Gainesville AI 411" in prompt
    assert "$999" not in prompt
    names = {t["name"] for t in agent.get_tools()}
    assert "create_change_request" not in names
    assert "search_events" in names


def test_end_call_sets_ended_in_owner_updates():
    _, agent, _, _ = _reload_mode("owner_updates")
    state = _state(agent)
    msg = agent._run_tool(state, "end_call", {})
    assert state.ended is True
    assert "end" in msg.lower()


def test_create_change_request_via_tool_runner(tmp_path, monkeypatch):
    """create_change_request hits changerequests store with tmp path + HTML outline."""
    cr_path = tmp_path / "change-requests.jsonl"
    sites = tmp_path / "sites"
    sites.mkdir()
    (sites / "cool-cafe.html").write_text(
        """<!DOCTYPE html>
<html><head><title>Cool Cafe | Coffee – Gainesville, FL</title></head>
<body>
  <h1>Cool Cafe</h1>
  <h2>Hours</h2>
  <p>Mon–Fri 7am–6pm</p>
  <h2>Contact</h2>
  <p>Call us at (352) 555-0100</p>
</body></html>
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CHANGE_REQUESTS_PATH", str(cr_path))
    monkeypatch.setenv("GENERATED_SITES_DIR", str(sites))

    # Clear cached store modules so env is re-read.
    for key in list(sys.modules):
        if key in (
            "changerequests",
            "lookup",
            "siteedit",
            "mcp_bridge",
        ) or key.startswith("changerequests."):
            del sys.modules[key]

    config, agent, _, bridge = _reload_mode("owner_updates")
    bridge.reset_for_tests()
    state = _state(agent, phone="+13555550100")

    outline_raw = agent._run_tool(state, "get_site_outline", {"slug": "cool-cafe"})
    assert "not available" not in outline_raw.lower()
    assert "not wired" not in outline_raw.lower()
    outline = json.loads(outline_raw)
    assert outline.get("found") is True
    assert outline.get("slug") == "cool-cafe"

    items = [
        {
            "type": "hours",
            "target": "Hours",
            "before": "Mon–Fri 7am–6pm",
            "after": "Mon–Sat 8am–8pm",
        }
    ]
    created_raw = agent._run_tool(
        state,
        "create_change_request",
        {
            "business_slug": "cool-cafe",
            "summary": "Update weekday hours to include Saturday evenings",
            "items": items,
            "confirmation_spoken": True,
        },
    )
    assert "not available" not in created_raw.lower()
    created = json.loads(created_raw)
    assert created.get("created") is True
    assert created.get("id", "").startswith("cr-")
    assert created.get("item_count") == 1
    # caller_phone defaulted from CallState
    req = created.get("request") or {}
    assert req.get("caller_phone") == "+13555550100"
    assert req.get("confirmation_spoken") is True

    # JSON string items also accepted
    created2 = json.loads(
        agent._run_tool(
            state,
            "create_change_request",
            {
                "business_slug": "cool-cafe",
                "summary": "Phone line update",
                "items": json.dumps(
                    [{"type": "phone", "after": "352-555-0199"}]
                ),
                "confirmation_spoken": True,
            },
        )
    )
    assert created2.get("created") is True

    listed = json.loads(
        agent._run_tool(state, "list_open_change_requests", {"slug": "cool-cafe"})
    )
    assert listed.get("count", 0) >= 2

    cancel = json.loads(
        agent._run_tool(
            state, "cancel_change_request", {"request_id": created["id"]}
        )
    )
    assert cancel.get("cancelled") is True or cancel.get("already_cancelled") is True


def test_sales_mode_unknown_owner_tool():
    _, agent, _, _ = _reload_mode("sales")
    state = _state(agent)
    out = agent._run_tool(state, "create_change_request", {"business_slug": "x"})
    assert "Unknown tool" in out
