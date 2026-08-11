"""Inbound SMS webhook + demo-link email tool."""

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


def _reload_sales():
    os.environ.pop("AGENT_MODE", None)
    os.environ.pop("VOICE_AGENT_MODE", None)
    # Avoid require() side effects at server import by ensuring keys exist.
    os.environ.setdefault("TWILIO_ACCOUNT_SID", "ACtest")
    os.environ.setdefault("TWILIO_AUTH_TOKEN", "token")
    os.environ.setdefault("TWILIO_PHONE_NUMBER", "+13555550100")
    os.environ.setdefault("PUBLIC_BASE_URL", "https://example.test")
    os.environ.setdefault("VALIDATE_TWILIO_WEBHOOKS", "0")
    os.environ.setdefault("VOICE_BACKEND", "pipeline")
    os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")
    os.environ.setdefault("DEEPINFRA_API_KEY", "di-test")
    os.environ.setdefault("CALL_DB", "0")

    import config as config_mod
    import agent as agent_mod
    import mailer as mailer_mod

    importlib.reload(config_mod)
    importlib.reload(mailer_mod)
    importlib.reload(agent_mod)
    return config_mod, agent_mod, mailer_mod


@pytest.fixture(autouse=True)
def _restore():
    yield
    _reload_sales()


class _Biz:
    name = "Cool Cafe"
    category = "cafe"
    address = "1 Main"
    rating = "4.5"
    demo_url = "https://example.com/cool-cafe.html"
    slug = "cool-cafe"


def test_sales_tools_include_email():
    _, agent, _ = _reload_sales()
    names = {t["name"] for t in agent.get_tools()}
    assert "send_demo_link_sms" in names
    assert "send_demo_link_email" in names
    prompt = agent.system_prompt(_Biz(), "inbound", "+13555550199")
    assert "send_demo_link_email" in prompt
    sms_prompt = agent.system_prompt(_Biz(), "sms", "+13555550199")
    assert "INBOUND SMS" in sms_prompt or "SMS" in sms_prompt


def test_send_demo_link_email_success(monkeypatch):
    _, agent, mailer = _reload_sales()
    state = agent.CallState(
        call_sid="TEST-EMAIL",
        business=_Biz(),
        direction="inbound",
        caller_number="+13555550199",
    )
    state.llm = mock.Mock()

    monkeypatch.setattr(
        mailer,
        "send_email",
        lambda **kw: {"sent": True, "provider": "resend", "id": "re_1", "to": kw["to"]},
    )
    out = agent._run_tool(
        state, "send_demo_link_email", {"email": "owner@coolcafe.example"}
    )
    assert "sent" in out.lower()
    assert "owner@coolcafe.example" in out


def test_send_demo_link_email_invalid():
    _, agent, _ = _reload_sales()
    state = agent.CallState(
        call_sid="TEST-EMAIL-BAD",
        business=_Biz(),
        direction="inbound",
        caller_number="+13555550199",
    )
    state.llm = mock.Mock()
    out = agent._run_tool(state, "send_demo_link_email", {"email": "not-an-email"})
    assert "error" in out.lower() or "valid" in out.lower()


def test_sms_inbound_webhook_replies(monkeypatch):
    _reload_sales()
    # Import server after env is set so require() passes.
    # Ensure voice-agent dir is at front of sys.path so 'import server' resolves
    # to voice-agent/server.py, not mcp-server/server.py (especially after test_mcp_http runs).
    va = str(AGENT_DIR)
    if va in sys.path:
        sys.path.remove(va)
    sys.path.insert(0, va)
    import server as server_mod

    importlib.reload(server_mod)
    server_mod.SMS_SESSIONS.clear()
    server_mod.CALLS.clear()

    def fake_turn(state, user_speech, on_sentence=None):
        state.llm = mock.Mock()
        if user_speech and "email" in user_speech.lower():
            return "Sure — what's the best email for the demo link?"
        return "Hi — I'm an AI for Noah. Want the free demo site link by text?"

    monkeypatch.setattr(server_mod, "run_turn", fake_turn)
    monkeypatch.setattr(server_mod, "flush_call_transcript", lambda *a, **k: {})

    from fastapi.testclient import TestClient

    client = TestClient(server_mod.app)
    r = client.post(
        "/sms/inbound",
        data={
            "From": "+13555550199",
            "Body": "Hey what's this number?",
            "MessageSid": "SMtest001",
        },
    )
    assert r.status_code == 200
    assert "application/xml" in r.headers.get("content-type", "")
    assert "demo" in r.text.lower() or "AI" in r.text or "Noah" in r.text
    assert "+13555550199" in server_mod.SMS_SESSIONS

    # Multi-turn keeps the same session
    r2 = client.post(
        "/sms/inbound",
        data={
            "From": "+13555550199",
            "Body": "Can you email it?",
            "MessageSid": "SMtest002",
        },
    )
    assert r2.status_code == 200
    assert "email" in r2.text.lower()
    assert server_mod.SMS_SESSIONS["+13555550199"][0].call_sid == "SMtest001"


def test_mailer_not_configured(monkeypatch):
    config, _, mailer = _reload_sales()
    monkeypatch.setattr(config, "EMAIL_FROM", "")
    monkeypatch.setattr(config, "RESEND_API_KEY", "")
    monkeypatch.setattr(config, "SMTP_HOST", "")
    result = mailer.send_email(
        to="a@b.co", subject="t", text_body="hi"
    )
    assert result["sent"] is False
    assert "not configured" in result["error"]
