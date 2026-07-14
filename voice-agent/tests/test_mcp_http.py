"""HTTP / auto MCP bridge backends for AI 411 tools (#51)."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from unittest import mock

import httpx
import pytest

AGENT_DIR = Path(__file__).resolve().parent.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


def _reload_bridge_with_env(**env: str | None):
    """Set env keys (None = delete), reload config + mcp_bridge, reset cache."""
    keys = ("MCP_MODE", "MCP_URL", "MCP_AUTH_TOKEN", "AGENT_MODE", "VOICE_AGENT_MODE")
    for k in keys:
        if k in env:
            v = env[k]
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    # Defaults for a clean bridge test.
    if "AGENT_MODE" not in env:
        os.environ.setdefault("AGENT_MODE", "ai411")

    import config as config_mod
    import ai411 as ai411_mod
    import mcp_bridge as bridge_mod

    importlib.reload(config_mod)
    importlib.reload(ai411_mod)
    importlib.reload(bridge_mod)
    bridge_mod.reset_for_tests()
    return config_mod, ai411_mod, bridge_mod


@pytest.fixture
def restore_sales_mode():
    yield
    _reload_bridge_with_env(AGENT_MODE="sales", MCP_MODE="inproc", MCP_URL=None, MCP_AUTH_TOKEN=None)


def _mcp_response(result: dict, *, status: int = 200, session: str = "sess-1") -> httpx.Response:
    body = {"jsonrpc": "2.0", "id": 1, "result": result}
    return httpx.Response(
        status,
        json=body,
        headers={"content-type": "application/json", "mcp-session-id": session},
        request=httpx.Request("POST", "https://mcp.example/mcp"),
    )


def _tool_call_result_payload(data: dict) -> dict:
    """FastMCP-style CallToolResult with text JSON content."""
    return {
        "content": [{"type": "text", "text": json.dumps(data)}],
        "isError": False,
    }


class TestHttpBackend:
    def test_http_tools_call_returns_json(self, restore_sales_mode):
        cfg, _, bridge = _reload_bridge_with_env(
            MCP_MODE="http",
            MCP_URL="https://mcp.example/mcp",
            MCP_AUTH_TOKEN="secret-token",
        )
        assert cfg.MCP_MODE == "http"

        tool_payload = {"ok": True, "results": [{"slug": "cool-cafe"}], "count": 1}
        calls: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content.decode())
            calls.append(payload)
            method = payload.get("method")
            assert request.headers.get("authorization") == "Bearer secret-token"
            if method == "initialize":
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": payload["id"],
                        "result": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "serverInfo": {"name": "demo", "version": "0"},
                        },
                    },
                    headers={
                        "content-type": "application/json",
                        "mcp-session-id": "sess-abc",
                    },
                    request=request,
                )
            if method == "notifications/initialized":
                return httpx.Response(202, request=request)
            if method == "tools/call":
                assert payload["params"]["name"] == "search_business_knowledge"
                assert payload["params"]["arguments"]["query"] == "espresso"
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": payload["id"],
                        "result": _tool_call_result_payload(tool_payload),
                    },
                    headers={"content-type": "application/json"},
                    request=request,
                )
            return httpx.Response(500, text=f"unexpected {method}", request=request)

        transport = httpx.MockTransport(handler)
        with httpx.Client(transport=transport, timeout=5.0) as client:
            out = bridge.call_mcp_tool_http(
                "search_business_knowledge",
                {"query": "espresso", "limit": 3},
                url="https://mcp.example/mcp",
                token="secret-token",
                client=client,
            )
        assert out == tool_payload
        methods = [c["method"] for c in calls]
        assert "initialize" in methods
        assert "tools/call" in methods

    def test_run_ai411_tool_http_mode(self, restore_sales_mode):
        _, _, bridge = _reload_bridge_with_env(
            MCP_MODE="http",
            MCP_URL="https://mcp.example/mcp",
            MCP_AUTH_TOKEN="tok",
        )

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content.decode())
            method = payload.get("method")
            if method == "initialize":
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": payload["id"],
                        "result": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "serverInfo": {"name": "demo", "version": "0"},
                        },
                    },
                    headers={
                        "content-type": "application/json",
                        "mcp-session-id": "s1",
                    },
                    request=request,
                )
            if method == "notifications/initialized":
                return httpx.Response(202, request=request)
            if method == "tools/call":
                assert payload["params"]["name"] == "search_events"
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": payload["id"],
                        "result": _tool_call_result_payload(
                            {"ok": True, "events": [{"id": "e1"}], "count": 1}
                        ),
                    },
                    headers={"content-type": "application/json"},
                    request=request,
                )
            return httpx.Response(500, request=request)

        transport = httpx.MockTransport(handler)
        real_client_cls = httpx.Client

        def client_factory(*args, **kwargs):
            kwargs = dict(kwargs)
            kwargs["transport"] = transport
            return real_client_cls(*args, **kwargs)

        with mock.patch.object(bridge.httpx, "Client", side_effect=client_factory):
            raw = bridge.run_ai411_tool(
                "search_events", {"query": "music", "limit": 2}, caller_number="+1"
            )
        data = json.loads(raw)
        assert data.get("ok") is True
        assert data.get("count") == 1

    def test_no_token_speakable_unauthorized(self, restore_sales_mode):
        _, _, bridge = _reload_bridge_with_env(
            MCP_MODE="http",
            MCP_URL="https://mcp.example/mcp",
            MCP_AUTH_TOKEN="",
        )
        raw = bridge.run_ai411_tool("lookup_business", {"query": "x"})
        data = json.loads(raw)
        assert data.get("unauthorized") is True
        assert "authorized" in data.get("error", "").lower() or "token" in data.get(
            "error", ""
        ).lower()
        # Must not raise / crash
        assert isinstance(raw, str)

    def test_http_401_speakable(self, restore_sales_mode):
        _, _, bridge = _reload_bridge_with_env(
            MCP_MODE="http",
            MCP_URL="https://mcp.example/mcp",
            MCP_AUTH_TOKEN="bad",
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401,
                json={"error": "unauthorized"},
                request=request,
            )

        transport = httpx.MockTransport(handler)
        real_client_cls = httpx.Client

        def client_factory(*args, **kwargs):
            kwargs = dict(kwargs)
            kwargs["transport"] = transport
            return real_client_cls(*args, **kwargs)

        with mock.patch.object(bridge.httpx, "Client", side_effect=client_factory):
            raw = bridge.run_ai411_tool("lookup_business", {"query": "x"})
        data = json.loads(raw)
        assert data.get("unauthorized") is True


class TestAutoBackend:
    def test_auto_falls_back_to_http_when_import_fails(self, restore_sales_mode):
        _, _, bridge = _reload_bridge_with_env(
            MCP_MODE="auto",
            MCP_URL="https://mcp.example/mcp",
            MCP_AUTH_TOKEN="tok",
        )

        # Force inproc import failure.
        with mock.patch.object(
            bridge, "_load_modules", return_value="ImportError: no mcp-server"
        ):
            bridge.reset_for_tests()
            backend = bridge._resolve_backend()
            assert backend == "http"

            def handler(request: httpx.Request) -> httpx.Response:
                payload = json.loads(request.content.decode())
                method = payload.get("method")
                if method == "initialize":
                    return httpx.Response(
                        200,
                        json={
                            "jsonrpc": "2.0",
                            "id": payload["id"],
                            "result": {
                                "protocolVersion": "2024-11-05",
                                "capabilities": {},
                                "serverInfo": {"name": "demo", "version": "0"},
                            },
                        },
                        headers={
                            "content-type": "application/json",
                            "mcp-session-id": "s1",
                        },
                        request=request,
                    )
                if method == "notifications/initialized":
                    return httpx.Response(202, request=request)
                if method == "tools/call":
                    return httpx.Response(
                        200,
                        json={
                            "jsonrpc": "2.0",
                            "id": payload["id"],
                            "result": _tool_call_result_payload(
                                {"found": False, "suggestions": []}
                            ),
                        },
                        headers={"content-type": "application/json"},
                        request=request,
                    )
                return httpx.Response(500, request=request)

            transport = httpx.MockTransport(handler)
            real_client_cls = httpx.Client

            def client_factory(*args, **kwargs):
                kwargs = dict(kwargs)
                kwargs["transport"] = transport
                return real_client_cls(*args, **kwargs)

            with mock.patch.object(bridge.httpx, "Client", side_effect=client_factory):
                raw = bridge.run_ai411_tool("lookup_business", {"query": "zzz"})
            data = json.loads(raw)
            assert "found" in data

    def test_auto_uses_inproc_when_import_ok(self, restore_sales_mode, tmp_path, monkeypatch):
        # Point knowledge at empty tmp so import still works if mcp-server present.
        monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path))
        monkeypatch.setenv("EVENTS_PATH", str(tmp_path / "events.json"))
        monkeypatch.setenv("CALLERS_PATH", str(tmp_path / "callers.json"))
        monkeypatch.setenv("BROADCASTS_PATH", str(tmp_path / "broadcasts.jsonl"))
        _, _, bridge = _reload_bridge_with_env(
            MCP_MODE="auto",
            MCP_URL="https://mcp.example/mcp",
            MCP_AUTH_TOKEN="tok",
        )
        # If monorepo mcp-server is importable, auto should pick inproc.
        err = bridge._load_modules()
        if err:
            pytest.skip(f"mcp-server not importable in this env: {err}")
        bridge.reset_for_tests()
        assert bridge._resolve_backend() == "inproc"


class TestInprocStillDefault:
    def test_default_mode_is_inproc(self, restore_sales_mode):
        cfg, _, bridge = _reload_bridge_with_env(
            MCP_MODE=None,
            MCP_URL=None,
            MCP_AUTH_TOKEN=None,
        )
        # After reload without MCP_MODE, default is inproc
        assert cfg.MCP_MODE == "inproc"
        bridge.reset_for_tests()
        assert bridge._resolve_backend() == "inproc"

    def test_map_tool_call_phone_fallback(self, restore_sales_mode):
        _, _, bridge = _reload_bridge_with_env(MCP_MODE="http", MCP_URL="x", MCP_AUTH_TOKEN="t")
        name, mapped = bridge._map_tool_call(
            "get_caller_profile", {}, caller_number="+13555550100"
        )
        assert name == "get_caller_profile"
        assert mapped["phone"] == "+13555550100"
