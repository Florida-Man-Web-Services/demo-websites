"""Consented AI 411 onboarding callback: TwiML URL + one Twilio dial.

Web-form request ≠ sales slug dialer. Outbound TwiML must not pass ?slug=.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parents[2]
VOICE = REPO / "voice-agent"
MCP = REPO / "mcp-server"
PHONE = "+1" + "352" + "555" + "0191"


def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("CUSTOMERS_PATH", str(tmp_path / "customers.json"))
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+1" + "844" + "903" + "0365")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://voice.example.test")
    monkeypatch.setenv("VALIDATE_TWILIO_WEBHOOKS", "0")
    monkeypatch.setenv("VOICE_BACKEND", "grok-realtime")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("DEEPINFRA_API_KEY", "di-test")
    monkeypatch.setenv("CALL_DB", "0")
    monkeypatch.setenv("AGENT_MODE", "auto")
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    for p in (str(VOICE), str(MCP)):
        if p in sys.path:
            sys.path.remove(p)
    sys.path.insert(0, str(MCP))
    sys.path.insert(0, str(VOICE))


@pytest.fixture()
def customers_mod(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    import customers

    importlib.reload(customers)
    return customers


def test_callback_twiml_url_has_no_slug():
    import callback_dial

    url = callback_dial.callback_twiml_url("https://voice.example.test/")
    assert url == "https://voice.example.test/voice/outbound"
    assert "slug=" not in url


def test_outbound_queued_without_slug_is_onboarding_not_sales(customers_mod):
    customers_mod.register_callback(PHONE, business_name="Cool Cafe")
    mode = customers_mod.resolve_mode(
        PHONE,
        direction="outbound",
        outbound_sales_slug=None,
        env_mode="auto",
    )
    assert mode == "onboarding"


def test_place_callback_dials_without_slug(customers_mod):
    customers_mod.register_callback(PHONE, business_name="Cool Cafe")
    fake_call = mock.Mock(sid="CAcallback1")
    fake = mock.Mock()
    fake.calls.create.return_value = fake_call

    import callback_dial

    importlib.reload(callback_dial)
    result = callback_dial.place_onboarding_callback(PHONE, twilio=fake)
    assert result["ok"] is True
    assert result["sid"] == "CAcallback1"
    kwargs = fake.calls.create.call_args.kwargs
    assert kwargs["to"] == PHONE
    assert kwargs["url"] == "https://voice.example.test/voice/outbound"
    assert "slug=" not in kwargs["url"]


def test_place_callback_refuses_resume_waitlist(customers_mod):
    customers_mod.register_callback(PHONE, source="resume_web", contact_name="Alex")
    fake = mock.Mock()
    import callback_dial

    importlib.reload(callback_dial)
    result = callback_dial.place_onboarding_callback(PHONE, twilio=fake)
    assert result["ok"] is False
    fake.calls.create.assert_not_called()


def test_place_callback_refuses_unknown_phone(customers_mod):
    fake = mock.Mock()
    import callback_dial

    importlib.reload(callback_dial)
    result = callback_dial.place_onboarding_callback(
        "+1" + "352" + "555" + "0000", twilio=fake
    )
    assert result["ok"] is False
    fake.calls.create.assert_not_called()


def test_voice_outbound_without_slug_streams_to_number(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    import customers

    importlib.reload(customers)
    customers.register_callback(PHONE, business_name="Cool Cafe")

    import config
    import agent
    import server as server_mod

    importlib.reload(config)
    importlib.reload(agent)
    importlib.reload(server_mod)
    monkeypatch.setattr(server_mod, "_prime_xai", lambda *a, **k: None)

    from fastapi.testclient import TestClient

    client = TestClient(server_mod.app)
    r = client.post(
        "/voice/outbound",
        data={"CallSid": "CAtestout", "To": PHONE},
    )
    assert r.status_code == 200
    body = r.text
    assert "<Hangup" not in body
    assert PHONE in body
    assert "outbound" in body
    assert "slug" not in body.lower()


def test_register_ai411_web_places_callback(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    import customers

    importlib.reload(customers)
    import config
    import agent
    import server as server_mod

    importlib.reload(config)
    importlib.reload(agent)
    importlib.reload(server_mod)

    placed = {}

    def fake_place(phone, **kwargs):
        placed["phone"] = phone
        return {"ok": True, "sid": "CAfromregister", "to": phone}

    monkeypatch.setattr(
        "callback_dial.place_onboarding_callback", fake_place, raising=False
    )
    # Server may import the symbol; patch both.
    if hasattr(server_mod, "place_onboarding_callback"):
        monkeypatch.setattr(server_mod, "place_onboarding_callback", fake_place)

    from fastapi.testclient import TestClient

    client = TestClient(server_mod.app)
    r = client.post(
        "/api/onboarding/register",
        json={"phone": PHONE, "business_name": "Cool Cafe", "source": "ai411_web"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["customer"]["status"] == "callback_queued"
    assert placed.get("phone") == PHONE
    assert body.get("call_sid") == "CAfromregister"


def test_register_resume_web_does_not_dial(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    import customers

    importlib.reload(customers)
    import config
    import agent
    import server as server_mod

    importlib.reload(config)
    importlib.reload(agent)
    importlib.reload(server_mod)

    fake_place = mock.Mock(return_value={"ok": True, "sid": "CAshouldnot"})
    monkeypatch.setattr(
        "callback_dial.place_onboarding_callback", fake_place, raising=False
    )
    if hasattr(server_mod, "place_onboarding_callback"):
        monkeypatch.setattr(server_mod, "place_onboarding_callback", fake_place)

    from fastapi.testclient import TestClient

    client = TestClient(server_mod.app)
    r = client.post(
        "/api/onboarding/register",
        json={"phone": PHONE, "source": "resume_web", "contact_name": "Alex"},
    )
    assert r.status_code == 200
    assert r.json()["customer"]["status"] == "resume_waitlist"
    fake_place.assert_not_called()
