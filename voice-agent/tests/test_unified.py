"""AGENT_MODE=unified: one public number — AI 411 + caller-ID-gated owner tools."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

AGENT_DIR = Path(__file__).resolve().parent.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

import agent
import ai411
import config
import owner_updates
import unified
from businesses import Business

OWNER_PHONE = "(352) 555-0134"
OWNER_CALLER_ID = "+13525550134"
STRANGER_CALLER_ID = "+13525559999"


def _biz(**kw):
    kw.setdefault("name", "Test Cafe")
    kw.setdefault("phone", OWNER_PHONE)
    kw.setdefault("demo_url", "https://x.test/d/")
    return Business(**kw)


@pytest.fixture
def unified_mode(monkeypatch):
    monkeypatch.setattr(config, "AGENT_MODE", "unified")


def test_tool_union_has_no_duplicate_names():
    names = [t["name"] for t in unified.TOOLS]
    assert len(names) == len(set(names))
    assert {"search_events", "search_business_knowledge"} <= set(names)  # 411 side
    assert {"create_change_request", "get_site_outline"} <= set(names)  # owner side


def test_owner_tool_names_exclude_shared_surface():
    # Shared names stay on the 411 dispatch path.
    assert "lookup_business" not in unified.OWNER_TOOL_NAMES
    assert "send_sms_links" not in unified.OWNER_TOOL_NAMES
    assert "end_call" not in unified.OWNER_TOOL_NAMES
    assert "create_change_request" in unified.OWNER_TOOL_NAMES


def test_caller_owns_matches_normalized_phone():
    assert unified.caller_owns(_biz(), OWNER_CALLER_ID)
    assert not unified.caller_owns(_biz(), STRANGER_CALLER_ID)
    assert not unified.caller_owns(_biz(phone=""), "")  # UNKNOWN_BUSINESS shape
    assert not unified.caller_owns(None, OWNER_CALLER_ID)


def test_prompt_grants_owner_access_on_caller_id_match(unified_mode):
    prompt = agent.system_prompt(_biz(), "inbound", OWNER_CALLER_ID)
    assert "Gainesville AI 411" in prompt  # still the 411 persona
    assert "OWNER ACCESS" in prompt
    assert "Test Cafe" in prompt
    assert "$999" not in prompt  # never the sales pitch


def test_prompt_withholds_owner_access_from_strangers(unified_mode):
    prompt = agent.system_prompt(_biz(), "inbound", STRANGER_CALLER_ID)
    assert "Gainesville AI 411" in prompt
    assert "OWNER ACCESS" not in prompt
    assert "caller ID doesn't match" in prompt


def test_mode_dispatch_selects_unified_surface(unified_mode):
    assert agent.get_tools() is unified.TOOLS
    assert agent.get_openers() is unified.OPENERS


def _state(caller):
    return agent.CallState(
        call_sid="CA-test", business=_biz(), direction="inbound", caller_number=caller
    )


def test_run_tool_routes_411_names_to_411_bridge(unified_mode, monkeypatch):
    import mcp_bridge

    monkeypatch.setattr(
        mcp_bridge, "run_ai411_tool", lambda name, args, **kw: f"411:{name}"
    )
    result = agent._run_tool(_state(STRANGER_CALLER_ID), "search_events", {})
    assert result == "411:search_events"


def test_run_tool_gates_owner_tools_on_caller_id(unified_mode, monkeypatch):
    import mcp_bridge

    monkeypatch.setattr(
        mcp_bridge, "run_owner_updates_tool", lambda name, args, **kw: f"owner:{name}"
    )
    # Owner calling from the business line: allowed through.
    ok = agent._run_tool(_state(OWNER_CALLER_ID), "create_change_request", {})
    assert ok == "owner:create_change_request"
    # Stranger: hard-refused in code, not just in the prompt.
    refused = agent._run_tool(_state(STRANGER_CALLER_ID), "create_change_request", {})
    assert "own phone line" in refused
    assert "owner:" not in refused


def test_owner_prompt_embeds_their_site_text(unified_mode, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GENERATED_SITES_DIR", tmp_path)
    biz = _biz()
    (tmp_path / f"{biz.slug}.html").write_text(
        "<html><body><h1>Test Cafe</h1><p>Cold brew special</p></body></html>"
    )
    prompt = agent.system_prompt(biz, "inbound", OWNER_CALLER_ID)
    assert "Cold brew special" in prompt
