"""Owner site tools route to MCP HTTP when MCP_URL + token are set."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from unittest import mock

AGENT_DIR = Path(__file__).resolve().parent.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


def _reload(**env: str | None):
    keys = ("MCP_MODE", "MCP_URL", "MCP_AUTH_TOKEN", "AGENT_MODE", "VOICE_AGENT_MODE")
    for key in keys:
        if key in env:
            value = env[key]
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    os.environ.setdefault("AGENT_MODE", "owner_updates")
    import config as config_mod
    import mcp_bridge as bridge_mod

    importlib.reload(config_mod)
    importlib.reload(bridge_mod)
    bridge_mod.reset_for_tests()
    return config_mod, bridge_mod


def test_apply_change_request_uses_mcp_http_when_configured():
    _, bridge = _reload(
        AGENT_MODE="owner_updates",
        MCP_MODE="inproc",
        MCP_URL="http://demo-mcp.theswamp.svc:8036/mcp",
        MCP_AUTH_TOKEN="secret-token",
    )
    with mock.patch.object(
        bridge,
        "call_mcp_tool_http",
        return_value={"applied": True, "status": "shipped", "pr_url": "https://github.com/x/y/pull/1"},
    ) as http:
        out = json.loads(
            bridge.run_owner_updates_tool(
                "apply_change_request",
                {"request_id": "cr-abc"},
                caller_number="+13520000000",
            )
        )
    http.assert_called_once()
    name, args = http.call_args.args[:2]
    assert name == "apply_change_request"
    assert args["request_id"] == "cr-abc"
    assert http.call_args.kwargs["timeout"] == bridge._SHIP_HTTP_TIMEOUT
    assert out["applied"] is True
    assert out["pr_url"].endswith("/pull/1")


def test_lookup_business_stays_inproc_when_ship_http_is_configured():
    _, bridge = _reload(
        AGENT_MODE="owner_updates",
        MCP_MODE="inproc",
        MCP_URL="http://demo-mcp.theswamp.svc:8036/mcp",
        MCP_AUTH_TOKEN="secret-token",
    )
    with mock.patch.object(bridge, "call_mcp_tool_http") as http, mock.patch.object(
        bridge, "_load_owner_modules", return_value=None
    ):
        bridge._owner_mods["find_business"] = lambda query: {"found": True, "query": query}
        bridge._owner_mods["changerequests"] = mock.Mock()
        out = json.loads(
            bridge.run_owner_updates_tool(
                "lookup_business",
                {"query": "+13520000000"},
                caller_number="+13520000000",
            )
        )
    http.assert_not_called()
    assert out["found"] is True


def test_create_change_request_stays_inproc_without_mcp_url():
    _, bridge = _reload(
        AGENT_MODE="owner_updates",
        MCP_MODE="inproc",
        MCP_URL=None,
        MCP_AUTH_TOKEN=None,
    )
    with mock.patch.object(bridge, "call_mcp_tool_http") as http, mock.patch.object(
        bridge, "_load_owner_modules", return_value=None
    ):
        cr = mock.Mock()
        cr.create_change_request.return_value = {"created": True, "id": "cr-local"}
        bridge._owner_mods["changerequests"] = cr
        bridge._owner_mods["find_business"] = lambda query: {"found": False}
        out = json.loads(
            bridge.run_owner_updates_tool(
                "create_change_request",
                {
                    "business_slug": "cool-cafe",
                    "summary": "hours",
                    "items": [{"type": "hours", "after": "9-5"}],
                    "confirmation_spoken": True,
                },
                caller_number="+13520000000",
            )
        )
    http.assert_not_called()
    assert out["created"] is True
    cr.create_change_request.assert_called_once()
