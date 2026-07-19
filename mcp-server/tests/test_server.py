import importlib

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "test-token-123")
    import server
    importlib.reload(server)
    with TestClient(server.build_app()) as c:
        yield c


def test_health_needs_no_auth(client):
    r = client.get("/health")
    assert r.status_code == 200


def test_mcp_rejects_missing_token(client):
    r = client.post("/mcp", json={})
    assert r.status_code == 401


def test_mcp_rejects_wrong_token(client):
    r = client.post("/mcp", json={}, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_mcp_accepts_right_token(client):
    # A garbage body with the right token must get past auth (not 401);
    # the MCP layer itself will reject the malformed request.
    r = client.post(
        "/mcp", json={}, headers={"Authorization": "Bearer test-token-123"}
    )
    assert r.status_code != 401


def test_mcp_production_host_not_rejected(client):
    # Regression for the 421 Invalid Host bug: FastMCP's default DNS-rebinding
    # allowlist only covers 127.0.0.1/localhost/::1, so a real request with
    # Host: mcp.flmanbiosci.net (as sent in production) would 421 unless
    # transport_security is configured with the production hostname.
    r = client.post(
        "/mcp",
        json={},
        headers={
            "Authorization": "Bearer test-token-123",
            "Host": "mcp.flmanbiosci.net",
        },
    )
    assert r.status_code not in (401, 421)

    # Discriminating check: an unlisted Host must still 421. Without this,
    # the assertion above would pass vacuously if DNS-rebinding protection
    # weren't engaged at all — this proves the allowlist is actually wired.
    bad = client.post(
        "/mcp",
        json={},
        headers={
            "Authorization": "Bearer test-token-123",
            "Host": "evil.example.com",
        },
    )
    assert bad.status_code == 421


def test_tools_registered(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "t")
    import server
    importlib.reload(server)
    import anyio
    tools = anyio.run(server.mcp.list_tools)
    names = {t.name for t in tools}
    assert names == {
        "lookup_business", "get_pitch_info", "get_call_history",
        "log_call_outcome",
        "search_business_knowledge", "get_business_snapshot",
        "get_caller_profile", "update_caller_profile", "forget_caller",
        "add_caller_note",
        "create_change_request", "list_open_change_requests",
        "cancel_change_request", "get_site_outline",
        "get_change_request", "apply_change_request", "mark_request_shipped",
        "open_site_update_pr",
        "search_events", "get_event", "list_event_sources",
        "submit_event_broadcast", "submit_notice_broadcast",
        "list_recent_broadcasts", "report_broadcast", "delete_own_broadcast",
    }


def test_log_tool_resolves_business(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "CALL_LOG", tmp_path / "log.csv")
    monkeypatch.setenv("MCP_AUTH_TOKEN", "t")
    import server
    importlib.reload(server)
    import anyio
    result = anyio.run(server.log_call_outcome, "Ole Barn", "interested", "great call")
    assert result == {"logged": True}
    unknown = anyio.run(server.log_call_outcome, "zzzzqqqq", "interested", "n")
    assert unknown["logged"] is False
    assert "suggestions" in unknown


def test_empty_token_fails_closed(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "")
    import server
    importlib.reload(server)
    with TestClient(server.build_app()) as c:
        r = c.post("/mcp", json={}, headers={"Authorization": "Bearer "})
        assert r.status_code == 401
        assert c.get("/health").status_code == 200


def test_main_refuses_without_token(monkeypatch):
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    import server
    importlib.reload(server)
    with pytest.raises(SystemExit):
        server.main()
