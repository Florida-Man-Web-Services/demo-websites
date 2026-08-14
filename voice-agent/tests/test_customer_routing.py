"""Customer registry routing + onboarding funnel."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MCP = REPO / "mcp-server"
VOICE = REPO / "voice-agent"
sys.path.insert(0, str(MCP))
sys.path.insert(0, str(VOICE))


@pytest.fixture()
def customers_mod(tmp_path, monkeypatch):
    path = tmp_path / "customers.json"
    monkeypatch.setenv("CUSTOMERS_PATH", str(path))
    import customers

    importlib.reload(customers)
    return customers


def test_default_unknown_is_ai411(customers_mod):
    mode = customers_mod.resolve_mode("+13555550100", env_mode="auto")
    assert mode == "ai411"


def test_callback_queued_is_onboarding(customers_mod):
    r = customers_mod.register_callback("+13555550101", business_name="Cool Cafe")
    assert r["ok"] is True
    assert r["customer"]["status"] == "callback_queued"
    mode = customers_mod.resolve_mode("+13555550101", env_mode="auto")
    assert mode == "onboarding"


def test_requirements_ready_is_sales(customers_mod):
    customers_mod.register_callback("+13555550102", business_name="X")
    customers_mod.save_requirements(
        "+13555550102",
        requirements={"goals": ["bookings"], "pages": ["home", "contact"]},
        summary="Simple cafe site",
        business_name="X Cafe",
    )
    mode = customers_mod.resolve_mode("+13555550102", env_mode="auto")
    assert mode == "sales"
    brief = customers_mod.write_builder_brief("+13555550102")
    assert brief["ok"] is True
    assert Path(brief["path"]).is_file()


def test_paid_is_owner(customers_mod):
    customers_mod.upsert("+13555550103", status="demo_ready", demo_url="https://x.test/")
    customers_mod.mark_paid("+13555550103")
    assert customers_mod.resolve_mode("+13555550103", env_mode="auto") == "owner_updates"


def test_pinned_env_mode_wins(customers_mod):
    customers_mod.mark_paid("+13555550104")
    assert customers_mod.resolve_mode("+13555550104", env_mode="sales") == "sales"


def test_outbound_slug_forces_sales(customers_mod):
    mode = customers_mod.resolve_mode(
        "+13555550999",
        direction="outbound",
        outbound_sales_slug="ole-barn",
        env_mode="auto",
    )
    assert mode == "sales"


def test_agent_auto_tools_onboarding(tmp_path, monkeypatch):
    monkeypatch.setenv("CUSTOMERS_PATH", str(tmp_path / "c.json"))
    monkeypatch.setenv("AGENT_MODE", "auto")
    monkeypatch.setenv("CALL_DB", "0")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "t")
    monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+13555550000")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.test")
    monkeypatch.setenv("VALIDATE_TWILIO_WEBHOOKS", "0")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk")
    monkeypatch.setenv("DEEPINFRA_API_KEY", "di")
    monkeypatch.setenv("VOICE_BACKEND", "pipeline")

    import customers

    importlib.reload(customers)
    customers.register_callback("+13555550110", business_name="Test Biz")

    import config
    import agent
    import onboarding

    importlib.reload(config)
    importlib.reload(onboarding)
    importlib.reload(agent)

    mode, cust = agent.resolve_call_mode("+13555550110", direction="inbound")
    assert mode == "onboarding"
    assert cust.get("business_name") == "Test Biz"
    names = {t["name"] for t in agent.get_tools(mode)}
    assert "finalize_requirements" in names
    assert "queue_website_build" in names
    prompt = agent.system_prompt(
        agent.Business(name="Test Biz", slug="test-biz"),
        "inbound",
        "+13555550110",
        mode="onboarding",
        customer=cust,
    )
    assert "onboarding" in prompt.lower() or "interview" in prompt.lower()
    assert "$999" not in prompt


def test_resolve_call_mode_survives_missing_customers(tmp_path, monkeypatch):
    """Old images without mcp-server must not raise (instant hangup)."""
    monkeypatch.setenv("CUSTOMERS_PATH", str(tmp_path / "c.json"))
    monkeypatch.setenv("AGENT_MODE", "auto")
    monkeypatch.setenv("CALL_DB", "0")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "t")
    monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+13555550000")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.test")
    monkeypatch.setenv("VALIDATE_TWILIO_WEBHOOKS", "0")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk")
    monkeypatch.setenv("DEEPINFRA_API_KEY", "di")
    monkeypatch.setenv("VOICE_BACKEND", "pipeline")

    import config
    import agent

    importlib.reload(config)
    importlib.reload(agent)

    real_import = __import__

    def _block_customers(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "customers":
            raise ModuleNotFoundError("customers")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", _block_customers)
    mode, cust = agent.resolve_call_mode("+13555550199", direction="inbound")
    assert mode == "ai411"
    assert cust == {}
    mode2, _ = agent.resolve_call_mode(
        "+13555550199", direction="outbound", outbound_slug="ole-barn"
    )
    assert mode2 == "sales"
