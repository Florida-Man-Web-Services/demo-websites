"""AI 411 tools dispatch to mcp-server stores in-process (#51)."""

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
    if mode is None:
        os.environ.pop("AGENT_MODE", None)
        os.environ.pop("VOICE_AGENT_MODE", None)
    else:
        os.environ["AGENT_MODE"] = mode
        os.environ.pop("VOICE_AGENT_MODE", None)

    import config as config_mod
    import agent as agent_mod
    import ai411 as ai411_mod
    import mcp_bridge as bridge_mod

    importlib.reload(config_mod)
    importlib.reload(ai411_mod)
    importlib.reload(bridge_mod)
    bridge_mod.reset_for_tests()
    importlib.reload(agent_mod)
    return config_mod, agent_mod, ai411_mod, bridge_mod


class _Biz:
    name = "Test Biz"
    category = "cafe"
    address = "1 Main St"
    rating = "4.5"
    demo_url = "https://example.com/test-biz.html"
    slug = "test-biz"


def _state(agent_mod, phone: str = "+13525550100"):
    state = agent_mod.CallState(
        call_sid="TEST",
        business=_Biz(),
        direction="inbound",
        caller_number=phone,
    )
    state.llm = mock.Mock()
    return state


@pytest.fixture
def knowledge_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path))
    (tmp_path / "cool-cafe.html").write_text(
        """<!DOCTYPE html>
<html><head><title>Cool Cafe | Coffee – Gainesville, FL</title></head>
<body>
  <h1>Cool Cafe</h1>
  <p>Artisan espresso and pour-over coffee in downtown Gainesville.</p>
  <p>We serve pastries, breakfast sandwiches, and free Wi-Fi all day.</p>
  <h2>Hours</h2>
  <p>Open Monday through Friday 7am to 6pm. Closed Sunday.</p>
</body></html>
""",
        encoding="utf-8",
    )
    (tmp_path / "speedy-plumbing.html").write_text(
        """<!DOCTYPE html>
<html><head><title>Speedy Plumbing | Emergency Plumber</title></head>
<body>
  <h1>Speedy Plumbing</h1>
  <p>24/7 emergency plumbing for Gainesville and Alachua County.</p>
  <p>Drain cleaning, water heater repair, leak detection.</p>
</body></html>
""",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def callers_path(tmp_path, monkeypatch):
    path = tmp_path / "callers.json"
    monkeypatch.setenv("CALLERS_PATH", str(path))
    return path


@pytest.fixture
def broadcasts_path(tmp_path, monkeypatch):
    path = tmp_path / "broadcasts.jsonl"
    monkeypatch.setenv("BROADCASTS_PATH", str(path))
    return path


@pytest.fixture
def events_path(tmp_path, monkeypatch):
    path = tmp_path / "events.json"
    monkeypatch.setenv("EVENTS_PATH", str(path))
    return path


@pytest.fixture
def ai411_agent(knowledge_dir, callers_path, broadcasts_path, events_path):
    """Reload agent/bridge under AI 411 with store paths pointed at fixtures."""
    # Clear any previously imported mcp modules so env is re-read.
    for key in list(sys.modules):
        if key in (
            "knowledge",
            "events",
            "callers",
            "broadcasts",
            "lookup",
            "mcp_bridge",
        ) or key.startswith("knowledge.") or key.startswith("events."):
            del sys.modules[key]
    config, agent, ai411, bridge = _reload_mode("ai411")
    # Force re-import of store modules with new env.
    bridge.reset_for_tests()
    yield config, agent, ai411, bridge
    _reload_mode("sales")


def test_sales_mode_still_only_sales_tools():
    config, agent, _, _ = _reload_mode("sales")
    assert config.is_ai411() is False
    names = {t["name"] for t in agent.get_tools()}
    assert names == {"send_demo_link_sms", "log_call_outcome", "end_call"}
    state = _state(agent)
    out = agent._run_tool(state, "search_events", {"query": "x"})
    assert "Unknown tool" in out


def test_search_business_knowledge_live(ai411_agent):
    _, agent, _, _ = ai411_agent
    state = _state(agent)
    out = agent._run_tool(
        state, "search_business_knowledge", {"query": "espresso coffee Wi-Fi", "limit": 3}
    )
    assert "not available" not in out.lower()
    assert "not wired" not in out.lower()
    data = json.loads(out)
    assert data.get("ok") is True
    assert data.get("results")
    assert data["results"][0]["slug"] == "cool-cafe"


def test_search_events_seed_or_fixture(ai411_agent):
    _, agent, _, _ = ai411_agent
    state = _state(agent)
    out = agent._run_tool(state, "search_events", {"query": "", "limit": 10})
    data = json.loads(out)
    assert data.get("ok") is True
    assert data.get("count", 0) >= 1
    assert data.get("events")
    # get_event roundtrip
    eid = data["events"][0]["id"]
    detail = json.loads(agent._run_tool(state, "get_event", {"event_id": eid}))
    assert detail.get("found") is True


def test_caller_profile_update_roundtrip(ai411_agent):
    _, agent, _, _ = ai411_agent
    phone = "+13525550199"
    state = _state(agent, phone=phone)
    # Use caller_number default (omit phone in args).
    updated = json.loads(
        agent._run_tool(
            state,
            "update_caller_profile",
            {"patch": {"preferred_name": "Alex", "consent": {"memory_ok": True}}},
        )
    )
    assert updated.get("updated") is True
    profile = json.loads(agent._run_tool(state, "get_caller_profile", {}))
    assert profile.get("found") is True
    # memory_ok True → preferred name visible
    assert profile.get("preferred_name") == "Alex" or (
        profile.get("profile", {}).get("preferred_name") == "Alex"
    )


def test_notice_broadcast_list_roundtrip(ai411_agent):
    _, agent, _, _ = ai411_agent
    state = _state(agent, phone="+13525550222")
    submitted = json.loads(
        agent._run_tool(
            state,
            "submit_notice_broadcast",
            {"summary": "Free jazz at Bo Diddley tonight", "category": "music"},
        )
    )
    assert submitted.get("submitted") is True
    listed = json.loads(
        agent._run_tool(state, "list_recent_broadcasts", {"limit": 10, "category": "music"})
    )
    assert listed.get("ok") is True
    assert listed.get("count", 0) >= 1
    texts = " ".join(
        json.dumps(b) for b in listed.get("broadcasts", [])
    )
    assert "jazz" in texts.lower() or "Bo Diddley" in texts


def test_lookup_business_via_bridge(ai411_agent):
    """lookup_business should return a dict (found or suggestions), not a stub."""
    _, agent, _, _ = ai411_agent
    state = _state(agent)
    out = agent._run_tool(state, "lookup_business", {"query": "nonexistent-xyz-biz-999"})
    assert "not wired" not in out.lower()
    data = json.loads(out)
    assert "found" in data


def test_end_call_still_works(ai411_agent):
    _, agent, _, _ = ai411_agent
    state = _state(agent)
    msg = agent._run_tool(state, "end_call", {})
    assert state.ended is True
    assert "end" in msg.lower()
