"""Unit tests for AGENT_MODE=sales vs ai411 prompt/tool selection (#51)."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

AGENT_DIR = Path(__file__).resolve().parent.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


def _reload_mode(mode: str | None):
    """Reload config + agent modules under a given AGENT_MODE."""
    env = {}
    if mode is None:
        os.environ.pop("AGENT_MODE", None)
        os.environ.pop("VOICE_AGENT_MODE", None)
    else:
        os.environ["AGENT_MODE"] = mode
        os.environ.pop("VOICE_AGENT_MODE", None)

    # config reads env at import time
    import config as config_mod
    import agent as agent_mod
    import ai411 as ai411_mod

    importlib.reload(config_mod)
    importlib.reload(ai411_mod)
    importlib.reload(agent_mod)
    return config_mod, agent_mod, ai411_mod


class _Biz:
    name = "Test Biz"
    category = "cafe"
    address = "1 Main St"
    rating = "4.5"
    demo_url = "https://example.com/test-biz.html"
    slug = "test-biz"


def test_default_mode_is_sales():
    config, agent, _ = _reload_mode(None)
    assert config.AGENT_MODE == "sales"
    assert config.is_ai411() is False
    prompt = agent.system_prompt(_Biz(), "inbound", "+13555550100")
    assert "$999" in prompt
    assert "AI phone assistant" in prompt or "selling websites" in prompt
    assert "Gainesville AI 411" not in prompt
    names = {t["name"] for t in agent.get_tools()}
    assert names == {"send_demo_link_sms", "log_call_outcome", "end_call"}


def test_ai411_mode_prompt_and_tools():
    config, agent, ai411 = _reload_mode("ai411")
    assert config.AGENT_MODE == "ai411"
    assert config.is_ai411() is True
    prompt = agent.system_prompt(_Biz(), "inbound", "+13555550100")
    assert "Gainesville AI 411" in prompt
    assert "events, businesses, or post something" in prompt
    assert "911" in prompt
    assert "medical" in prompt.lower() or "No medical" in prompt
    assert "$999" not in prompt
    assert "selling websites" not in prompt
    names = {t["name"] for t in agent.get_tools()}
    expected = {
        "search_business_knowledge",
        "lookup_business",
        "search_events",
        "get_event",
        "get_caller_profile",
        "update_caller_profile",
        "forget_caller",
        "submit_event_broadcast",
        "submit_notice_broadcast",
        "list_recent_broadcasts",
        "send_sms_links",
        "end_call",
    }
    assert names == expected
    assert agent.get_openers() == ai411.OPENERS


def test_voice_agent_mode_alias():
    os.environ.pop("AGENT_MODE", None)
    os.environ["VOICE_AGENT_MODE"] = "ai411"
    import config as config_mod
    importlib.reload(config_mod)
    assert config_mod.AGENT_MODE == "ai411"
    os.environ.pop("VOICE_AGENT_MODE", None)
    importlib.reload(config_mod)


def test_end_call_sets_ended_in_ai411():
    _, agent, _ = _reload_mode("ai411")
    state = agent.CallState(
        call_sid="TEST",
        business=_Biz(),
        direction="inbound",
        caller_number="+13555550100",
    )
    # Avoid constructing a real LLM backend.
    state.llm = mock.Mock()
    msg = agent._run_tool(state, "end_call", {})
    assert state.ended is True
    assert "end" in msg.lower()


def test_ai411_stub_tools_speakable():
    _, agent, _ = _reload_mode("ai411")
    state = agent.CallState(
        call_sid="TEST",
        business=_Biz(),
        direction="inbound",
        caller_number="+13555550100",
    )
    state.llm = mock.Mock()
    out = agent._run_tool(state, "search_events", {"query": "free this weekend"})
    assert "not available" in out.lower() or "not wired" in out.lower() or "MCP" in out


@pytest.fixture(autouse=True)
def _restore_sales_mode():
    yield
    _reload_mode("sales")
