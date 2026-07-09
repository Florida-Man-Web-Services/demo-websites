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
    }


def test_log_tool_resolves_business(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "CALL_LOG", tmp_path / "log.csv")
    monkeypatch.setenv("MCP_AUTH_TOKEN", "t")
    import server
    importlib.reload(server)
    result = server.log_call_outcome("Ole Barn", "interested", "great call")
    assert result == {"logged": True}
    unknown = server.log_call_outcome("zzzzqqqq", "interested", "n")
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
