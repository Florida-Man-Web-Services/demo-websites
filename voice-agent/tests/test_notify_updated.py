"""Tests for POST /api/sms/notify-updated — sales dashboard notify button."""

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


def _setup():
    """Set env and import modules (reload to pick up env). Does NOT reload server
    to avoid triggering mcp-server imports that need the `mcp` package."""
    os.environ.setdefault("TWILIO_ACCOUNT_SID", "ACtest")
    os.environ.setdefault("TWILIO_AUTH_TOKEN", "token")
    os.environ.setdefault("TWILIO_PHONE_NUMBER", "+15555550100")
    os.environ.setdefault("PUBLIC_BASE_URL", "https://example.test")
    os.environ.setdefault("VALIDATE_TWILIO_WEBHOOKS", "0")
    os.environ.setdefault("VOICE_BACKEND", "pipeline")
    os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")
    os.environ.setdefault("DEEPINFRA_API_KEY", "di-test")
    os.environ.setdefault("CALL_DB", "0")

    import config as config_mod
    import agent as agent_mod

    importlib.reload(config_mod)
    importlib.reload(agent_mod)
    # Import server without reloading — avoids mcp-server import side effects.
    # Ensure voice-agent dir is at front of sys.path so 'import server' resolves
    # to voice-agent/server.py, not mcp-server/server.py.
    if str(AGENT_DIR) not in sys.path or sys.path[0] != str(AGENT_DIR):
        if str(AGENT_DIR) in sys.path:
            sys.path.remove(str(AGENT_DIR))
        sys.path.insert(0, str(AGENT_DIR))
    import server as server_mod
    return config_mod, agent_mod, server_mod


@pytest.fixture(autouse=True)
def _restore():
    yield


class _FakeMessage:
    def __init__(self, sid="SMnotify123"):
        self.sid = sid


class _FakeTwilio:
    def __init__(self):
        self.messages = mock.Mock()
        self.messages.create = mock.Mock(return_value=_FakeMessage())


def test_notify_updated_success(monkeypatch):
    _, agent, server = _setup()
    fake = _FakeTwilio()
    monkeypatch.setattr(agent, "_twilio", lambda: fake)

    from fastapi.testclient import TestClient
    client = TestClient(server.app)

    r = client.post(
        "/api/sms/notify-updated",
        json={"phone": "+15555551234", "demo_url": "https://floridamanweb.online/abc123/"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["sid"] == "SMnotify123"
    assert body["to"] == "+15555551234"
    fake.messages.create.assert_called_once()


def test_notify_updated_uses_custom_message(monkeypatch):
    _, agent, server = _setup()
    fake = _FakeTwilio()
    monkeypatch.setattr(agent, "_twilio", lambda: fake)

    from fastapi.testclient import TestClient
    client = TestClient(server.app)

    r = client.post(
        "/api/sms/notify-updated",
        json={"phone": "+15555551234", "message": "Custom update message"},
    )
    assert r.status_code == 200
    _, kwargs = fake.messages.create.call_args
    assert "Custom update message" in kwargs["body"]


def test_notify_updated_missing_phone():
    _, _, server = _setup()
    from fastapi.testclient import TestClient
    client = TestClient(server.app)

    r = client.post("/api/sms/notify-updated", json={"phone": ""})
    assert r.status_code == 400
    assert "phone" in r.json()["detail"].lower()


def test_notify_updated_twilio_failure(monkeypatch):
    _, agent, server = _setup()

    class _BoomTwilio:
        messages = mock.Mock()
        messages.create = mock.Mock(side_effect=Exception("Twilio API quota exceeded"))

    monkeypatch.setattr(agent, "_twilio", lambda: _BoomTwilio())

    from fastapi.testclient import TestClient
    client = TestClient(server.app)

    r = client.post(
        "/api/sms/notify-updated",
        json={"phone": "+15555551234", "demo_url": "https://example.com/"},
    )
    assert r.status_code == 500
    assert "quota" in r.json()["detail"].lower()
