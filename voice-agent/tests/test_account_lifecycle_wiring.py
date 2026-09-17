from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VOICE = ROOT / "voice-agent"
if str(VOICE) not in sys.path:
    sys.path.insert(0, str(VOICE))

import ai411
import owner_updates


FORBIDDEN = {
    "otp",
    "code",
    "caller_phone",
    "phone",
    "destination_phone",
    "account_id",
    "auth_context",
    "confirmed",
    "confirmation_boolean",
}


def _names(tools):
    return {tool["name"] for tool in tools}


def test_both_modes_expose_same_safe_lifecycle_surface():
    expected = {
        "request_account_step_up",
        "get_account_lifecycle_status",
        "prepare_client_page",
        "prepare_trusted_phone_add",
        "prepare_trusted_phone_removal",
        "prepare_client_page_removal",
        "cancel_account_operation",
    }
    assert expected <= _names(ai411.TOOLS)
    assert expected <= _names(owner_updates.TOOLS)
    for tool in [*ai411.TOOLS, *owner_updates.TOOLS]:
        if tool["name"] in expected:
            properties = set(tool["input_schema"].get("properties", {}))
            assert not properties & FORBIDDEN
            assert tool["input_schema"].get("additionalProperties") is False


def test_disabled_lifecycle_request_is_fail_closed(monkeypatch):
    monkeypatch.delenv("ACCOUNT_LIFECYCLE_ENABLED", raising=False)
    import agent
    from businesses import Business

    state = agent.CallState(
        call_sid="unit-test",
        business=Business(name="test", category="", demo_url="", slug="test"),
        direction="inbound",
        caller_number="+13528883741",
        mode="ai411",
    )
    result = agent._run_tool(
        state,
        "prepare_client_page",
        {"title": "x", "body": "y", "idempotency_key": "k"},
    )
    parsed = json.loads(result)
    assert parsed["state"] == "denied"
    assert parsed["code"] in {"feature_disabled", "transport_unavailable"}
