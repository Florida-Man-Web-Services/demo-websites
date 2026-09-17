from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VOICE = ROOT / "voice-agent"
if str(VOICE) not in sys.path:
    sys.path.insert(0, str(VOICE))


def _import_server(monkeypatch):
    for key in (
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_PHONE_NUMBER",
        "PUBLIC_BASE_URL",
        "DEEPINFRA_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.setenv(key, "test")
    monkeypatch.setenv("ACCOUNT_LIFECYCLE_ENABLED", "false")
    import server
    return server


def test_unknown_and_invalid_public_client_pages_are_no_store(monkeypatch):
    from fastapi.testclient import TestClient

    server = _import_server(monkeypatch)
    client = TestClient(server.app)
    for path in ("/clients/unknown-client", "/clients/Bad"):
        response = client.get(path)
        assert response.status_code == 404
        assert response.headers["cache-control"] == "no-store"
        assert response.content == b""


def test_public_route_has_no_mutation_endpoint():
    import server

    routes = {
        (getattr(route, "path", ""), tuple(sorted(getattr(route, "methods", set()) or set())))
        for route in server.app.routes
    }
    assert not any(path.startswith("/clients/") and "POST" in methods for path, methods in routes)
