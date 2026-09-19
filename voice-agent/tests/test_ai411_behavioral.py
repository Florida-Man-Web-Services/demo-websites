"""Behavioral regression gates for the public AI 411 call surface."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
from pathlib import Path
from unittest import mock

import httpx
import pytest

AGENT_DIR = Path(__file__).resolve().parent.parent
MCP_DIR = AGENT_DIR.parent / "mcp-server"
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


class _Biz:
    name = "Cool Cafe"
    category = "cafe"
    address = "1 Main St, Gainesville"
    rating = "4.5"
    demo_url = "https://example.test/cool-cafe"
    slug = "cool-cafe"
    phone = "+13525550199"


class _ScriptedBackend:
    """Small deterministic backend for exercising agent.run_turn."""

    def __init__(self, agent_mod, results):
        self._agent = agent_mod
        self._results = list(results)
        self.messages: list[dict] = []
        self.prompts: list[str] = []

    def has_history(self) -> bool:
        return bool(self.messages)

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_tool_results(self, results) -> None:
        self.messages.append(
            {"role": "tool", "content": [{"id": i, "text": t} for i, t in results]}
        )

    def stream(self, prompt: str, on_delta) -> object:
        self.prompts.append(prompt)
        result = self._results.pop(0)
        text = " ".join(result.text_parts)
        if text:
            on_delta(text)
        self.messages.append({"role": "assistant", "content": text})
        return result


@pytest.fixture
def ai411_modules(monkeypatch, tmp_path):
    """Load the real voice and MCP stores against isolated temporary files."""
    paths = {
        "CALLERS_PATH": tmp_path / "callers.json",
        "BROADCASTS_PATH": tmp_path / "broadcasts.jsonl",
        "EVENTS_PATH": tmp_path / "events.json",
        "QOTD_PATH": tmp_path / "qotd.json",
        "EVENT_INTERESTS_PATH": tmp_path / "event-interests.jsonl",
        "FOMO_NOTIFY_PATH": tmp_path / "fomo-notify.jsonl",
        "PERSONAL_PAGES_REGISTRY": tmp_path / "personal-pages.json",
        "PERSONAL_PAGES_DIR": tmp_path / "personal-pages",
        "CALL_DB": tmp_path / "calls.db",
        "CALL_LOG": tmp_path / "calls.csv",
        "KNOWLEDGE_DIR": tmp_path / "knowledge",
    }
    for key, value in paths.items():
        monkeypatch.setenv(key, str(value))
    monkeypatch.setenv("AGENT_MODE", "ai411")
    monkeypatch.setenv("MCP_MODE", "inproc")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://voice.example.test")
    monkeypatch.setenv("PERSONAL_PAGE_BASE_URL", "https://voice.example.test/me")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+18449030365")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("DEEPINFRA_API_KEY", "di-test")
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    monkeypatch.setenv("VALIDATE_TWILIO_WEBHOOKS", "0")

    (paths["KNOWLEDGE_DIR"]).mkdir()
    (paths["KNOWLEDGE_DIR"] / "cool-cafe.html").write_text(
        "<title>Cool Cafe</title><h1>Cool Cafe</h1>"
        "<p>Espresso, pastries, and free Wi-Fi.</p>",
        encoding="utf-8",
    )

    for name in (
        "knowledge",
        "events",
        "callers",
        "broadcasts",
        "lookup",
        "qotd",
        "fomo",
        "personal_pages",
        "mcp_bridge",
    ):
        sys.modules.pop(name, None)

    import config
    import ai411
    import mcp_bridge
    import agent

    importlib.reload(config)
    importlib.reload(ai411)
    importlib.reload(mcp_bridge)
    mcp_bridge.reset_for_tests()
    importlib.reload(agent)
    yield config, ai411, mcp_bridge, agent, paths


@pytest.fixture(autouse=True)
def _restore_mode(monkeypatch):
    yield
    monkeypatch.setenv("AGENT_MODE", "sales")


def _state(agent_mod, phone="+13525550100"):
    state = agent_mod.CallState(
        call_sid="CA-behavioral",
        business=_Biz(),
        direction="inbound",
        caller_number=phone,
        mode="ai411",
    )
    state.llm = mock.Mock()
    return state


def _tool(agent_mod, name, args=None, call_id="tool-1"):
    return agent_mod.ToolCall(call_id, name, args or {})


def test_directory_request_answers_from_lookup_tool(ai411_modules):
    _, _, bridge, agent, _ = ai411_modules
    calls = []

    def lookup(name, args, **kwargs):
        calls.append((name, args, kwargs))
        return json.dumps({"found": True, "name": "Cool Cafe", "address": "1 Main St"})

    bridge.run_ai411_tool = lookup
    backend = _ScriptedBackend(
        agent,
        [
            agent._TurnResult([], [_tool(agent, "lookup_business", {"query": "Cool Cafe"})]),
            agent._TurnResult(["Cool Cafe is at 1 Main St."], []),
        ],
    )
    state = _state(agent)
    state.llm = backend

    reply = agent.run_turn(state, "Where is Cool Cafe?")

    assert reply == "Cool Cafe is at 1 Main St."
    assert calls[0][0] == "lookup_business"
    assert calls[0][1] == {"query": "Cool Cafe"}


def test_event_broadcast_emits_and_can_be_retrieved(ai411_modules):
    _, _, _, agent, _ = ai411_modules
    state = _state(agent, phone="+13525550101")

    submitted = json.loads(
        agent._run_tool(
            state,
            "submit_notice_broadcast",
            {"summary": "Free jazz at Bo Diddley tonight", "category": "music"},
        )
    )
    assert submitted["submitted"] is True

    listed = json.loads(
        agent._run_tool(state, "list_recent_broadcasts", {"category": "music", "limit": 5})
    )
    assert listed["ok"] is True
    assert any("jazz" in json.dumps(row).lower() for row in listed["broadcasts"])


def test_silence_prompts_for_a_small_menu_not_qotd(ai411_modules):
    _, ai411, _, agent, _ = ai411_modules
    state = _state(agent)
    backend = _ScriptedBackend(
        agent,
        [agent._TurnResult(["Events, food, or a number?"], [])],
    )
    state.llm = backend

    assert agent.run_turn(state, None) == ai411.AI411_GREETING
    assert agent.run_turn(state, None) == "Events, food, or a number?"
    assert backend.messages[-2]["content"].startswith("<silence")
    assert "question of the day" in backend.prompts[-1].lower()
    assert "do not launch question of the day on silence" in backend.prompts[-1].lower()


def test_garbled_transcription_is_repeated_instead_of_guessed(ai411_modules):
    _, _, _, agent, _ = ai411_modules
    state = _state(agent)
    backend = _ScriptedBackend(
        agent,
        [agent._TurnResult(["I didn't catch that. Could you say it again?"], [])],
    )
    state.llm = backend

    reply = agent.run_turn(state, "wher iz the hippodrom tomorow qxv")

    assert "didn't catch" in reply
    assert not any("search_events" in str(message) for message in backend.messages)
    assert "garbled" in backend.prompts[0].lower()
    assert "confirm rather than guess" in backend.prompts[0].lower()


def test_tool_failure_stays_speakable_and_does_not_raise(ai411_modules, monkeypatch):
    _, _, bridge, _, _ = ai411_modules

    def broken_dispatch(*args, **kwargs):
        raise RuntimeError("private backend detail")

    monkeypatch.setattr(bridge, "_dispatch", broken_dispatch)
    raw = bridge.run_ai411_tool("lookup_business", {"query": "Cool Cafe"})

    assert isinstance(raw, str)
    assert "not available" in raw.lower()
    assert "traceback" not in raw.lower()
    assert "private backend detail" not in raw


def test_http_error_does_not_echo_private_response_body(ai411_modules):
    _, _, bridge, _, _ = ai411_modules
    secret = "private-backend-secret-7f1"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=f"<html>{secret}</html>", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = bridge.call_mcp_tool_http(
            "lookup_business",
            {"query": "Cool Cafe"},
            url="https://mcp.example.test/mcp",
            token="bearer-test",
            client=client,
        )

    assert result["ok"] is False
    assert "HTTP 500" in result["error"]
    assert secret not in result["error"]


def test_realtime_interruption_clears_queued_audio(ai411_modules):
    _, _, _, agent, _ = ai411_modules
    import realtime

    importlib.reload(realtime)
    state = _state(agent)
    twilio = _FakeSocket([], stream_sid="MS-interrupt", hold=True)
    xai = _FakeSocket([{"type": "input_audio_buffer.speech_started"}])

    asyncio.run(asyncio.wait_for(realtime.run_call(twilio, xai, state, primed=True), timeout=2))

    assert {"event": "clear", "streamSid": "MS-interrupt"} in twilio.sent


def test_personal_page_requires_explicit_page_opt_in_and_has_no_phone(ai411_modules):
    _, _, _, agent, paths = ai411_modules
    state = _state(agent, phone="+13525550102")

    remembered = json.loads(
        agent._run_tool(
            state,
            "update_caller_profile",
            {
                "patch": {
                    "consent": {"memory_ok": True},
                    "preferred_name": "Alex",
                    "preferences": {"interests": ["jazz"]},
                }
            },
        )
    )
    assert remembered["updated"] is True
    before = json.loads(agent._run_tool(state, "get_personal_page_status", {}))
    assert before["enabled"] is False

    opted_in = json.loads(
        agent._run_tool(state, "opt_in_personal_page", {"preferred_name": "Alex"})
    )
    assert opted_in["ok"] is True
    html = (paths["PERSONAL_PAGES_DIR"] / f"{opted_in['slug']}.html").read_text(
        encoding="utf-8"
    )
    assert "13525550102" not in html
    assert "+13525550102" not in html
    assert "Alex" in html


def test_personal_page_opt_out_is_idempotent_and_removes_page(ai411_modules):
    _, _, _, agent, paths = ai411_modules
    state = _state(agent, phone="+13525550103")
    opted_in = json.loads(agent._run_tool(state, "opt_in_personal_page", {}))
    page = paths["PERSONAL_PAGES_DIR"] / f"{opted_in['slug']}.html"
    assert page.exists()

    first = json.loads(agent._run_tool(state, "opt_out_personal_page", {}))
    second = json.loads(agent._run_tool(state, "opt_out_personal_page", {}))

    assert first["ok"] is True and second["ok"] is True
    assert not page.exists()
    status = json.loads(agent._run_tool(state, "get_personal_page_status", {}))
    assert status["enabled"] is False


def test_onboarding_callback_is_idempotent(ai411_modules, monkeypatch):
    _, _, _, _, paths = ai411_modules
    monkeypatch.setenv(
        "CUSTOMERS_PATH", str(paths["CALLERS_PATH"].with_name("customers.json"))
    )
    import customers
    import callback_dial

    importlib.reload(customers)
    importlib.reload(callback_dial)
    phone = "+13525550104"
    customers.register_callback(phone, business_name="Cool Cafe")
    fake_call = mock.Mock(sid="CA-idempotent")
    twilio = mock.Mock()
    twilio.calls.create.return_value = fake_call

    first = callback_dial.place_onboarding_callback(phone, twilio=twilio)
    second = callback_dial.place_onboarding_callback(phone, twilio=twilio)

    assert first["ok"] is True
    assert second["ok"] is True
    assert second.get("already_placed") is True
    assert customers.get(phone)["callback_sid"] == "CA-idempotent"
    twilio.calls.create.assert_called_once()


class _FakeSocket:
    def __init__(self, events, *, stream_sid="MS123", hold=False):
        self._events = list(events)
        self.sent = []
        self.stream_sid = stream_sid
        self._hold = hold
        self._closed = asyncio.Event()

    async def send(self, message):
        self.sent.append(message)

    def close(self):
        self._closed.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._events:
            return self._events.pop(0)
        if self._hold:
            await self._closed.wait()
        raise StopAsyncIteration
