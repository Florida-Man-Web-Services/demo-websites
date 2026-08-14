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
def qotd_path(tmp_path, monkeypatch):
    path = tmp_path / "qotd.json"
    monkeypatch.setenv("QOTD_PATH", str(path))
    return path


@pytest.fixture
def ai411_agent(knowledge_dir, callers_path, broadcasts_path, events_path, qotd_path, tmp_path, monkeypatch):
    """Reload agent/bridge under AI 411 with store paths pointed at fixtures."""
    interests = tmp_path / "event_interests.jsonl"
    notify = tmp_path / "fomo_notify.jsonl"
    monkeypatch.setenv("EVENT_INTERESTS_PATH", str(interests))
    monkeypatch.setenv("FOMO_NOTIFY_PATH", str(notify))
    # Clear any previously imported mcp modules so env is re-read.
    for key in list(sys.modules):
        if key in (
            "knowledge",
            "events",
            "callers",
            "broadcasts",
            "lookup",
            "qotd",
            "fomo",
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
    assert names == {"send_demo_link_sms", "send_demo_link_email", "log_call_outcome", "end_call"}
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


def test_qotd_tools_roundtrip(ai411_agent):
    _, agent, ai411, _ = ai411_agent
    names = {t["name"] for t in agent.get_tools()}
    for n in (
        "get_question_of_the_day",
        "answer_question_of_the_day",
        "suggest_question_of_the_day",
        "get_caller_people_profile",
        "match_events_for_profile",
    ):
        assert n in names
    assert "question of the day" in ai411.system_prompt(
        direction="inbound", caller_number="+13555550100"
    ).lower()
    phone = "+13555550188"
    state = _state(agent, phone=phone)
    q = json.loads(agent._run_tool(state, "get_question_of_the_day", {}))
    assert q.get("ok") is True
    assert q.get("text")
    ans = json.loads(
        agent._run_tool(
            state,
            "answer_question_of_the_day",
            {
                "answer": "I like outdoor music with friendly people",
                "question_id": q.get("question_id") or "",
            },
        )
    )
    assert ans.get("ok") is True and ans.get("recorded") is True
    sug = json.loads(
        agent._run_tool(
            state,
            "suggest_question_of_the_day",
            {
                "suggestion": (
                    "Who would you most want to meet at a community art night?"
                )
            },
        )
    )
    assert sug.get("ok") is True and sug.get("accepted") is True
    people = json.loads(agent._run_tool(state, "get_caller_people_profile", {}))
    assert people.get("found") is True
    matched = json.loads(
        agent._run_tool(state, "match_events_for_profile", {"when": "", "limit": 5})
    )
    assert matched.get("ok") is True
    assert isinstance(matched.get("events"), list)


def test_fomo_tools_roundtrip(ai411_agent):
    _, agent, ai411, _ = ai411_agent
    names = {t["name"] for t in agent.get_tools()}
    assert "express_event_interest" in names
    assert "list_event_interest_matches" in names
    prompt = ai411.system_prompt(direction="inbound", caller_number="+13525550100").lower()
    assert "fomo" in prompt

    phone_a = "+13525550188"
    phone_b = "+13525550189"
    state_a = _state(agent, phone=phone_a)
    state_b = _state(agent, phone=phone_b)

    # memory + fomo + sms for both
    for state in (state_a, state_b):
        upd = json.loads(
            agent._run_tool(
                state,
                "update_caller_profile",
                {
                    "patch": {
                        "consent": {"memory_ok": True, "fomo_ok": True},
                        "preferences": {
                            "interests": ["music"],
                            "sms_ok": True,
                        },
                    }
                },
            )
        )
        assert upd.get("updated") is True

    events = json.loads(agent._run_tool(state_a, "search_events", {"query": "jazz", "limit": 5}))
    assert events.get("ok") is True
    eid = (events.get("events") or [{}])[0].get("id")
    assert eid

    r1 = json.loads(
        agent._run_tool(state_a, "express_event_interest", {"event_id": eid})
    )
    assert r1.get("ok") is True and r1.get("recorded") is True

    r2 = json.loads(
        agent._run_tool(state_b, "express_event_interest", {"event_id": eid})
    )
    assert r2.get("ok") is True and r2.get("recorded") is True
    assert r2.get("peer_matches", 0) >= 2

    matches = json.loads(agent._run_tool(state_a, "list_event_interest_matches", {}))
    assert matches.get("ok") is True
    assert matches.get("count", 0) >= 1
    blob = json.dumps(matches)
    assert phone_b not in blob
    assert "not available" not in blob.lower()
    assert "not wired" not in blob.lower()