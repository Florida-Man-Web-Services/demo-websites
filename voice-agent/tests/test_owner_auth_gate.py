"""Phase 1 owner auth_level gates (voice_auth + CallState)."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

AGENT_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = AGENT_DIR.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


def _reload_owner():
    os.environ["AGENT_MODE"] = "owner_updates"
    os.environ.pop("VOICE_AGENT_MODE", None)
    os.environ["VOICE_AUTH_VENDOR"] = "none"
    os.environ["VOICE_ENROLL_REQUIRED_FOR_WRITE"] = "false"

    import config as config_mod
    import agent as agent_mod
    import owner_updates as owner_mod
    import mcp_bridge as bridge_mod
    import voice_auth as va_mod

    importlib.reload(config_mod)
    importlib.reload(va_mod)
    importlib.reload(owner_mod)
    importlib.reload(bridge_mod)
    bridge_mod.reset_for_tests()
    importlib.reload(agent_mod)
    return config_mod, agent_mod, va_mod, bridge_mod


class _Biz:
    name = "Test Biz"
    category = "cafe"
    address = "1 Main St"
    rating = "4.5"
    demo_url = "https://example.com/test-biz.html"
    slug = "test-biz"


@pytest.fixture(autouse=True)
def _restore():
    yield
    os.environ["AGENT_MODE"] = "sales"
    import config, agent, owner_updates, mcp_bridge, voice_auth

    importlib.reload(config)
    importlib.reload(voice_auth)
    importlib.reload(owner_updates)
    importlib.reload(mcp_bridge)
    mcp_bridge.reset_for_tests()
    importlib.reload(agent)


def test_compute_initial_auth_legacy_unknown_phone(tmp_path, monkeypatch):
    monkeypatch.setenv("CUSTOMERS_PATH", str(tmp_path / "c.json"))
    (tmp_path / "c.json").write_text("{}\n", encoding="utf-8")
    _, _, va, _ = _reload_owner()
    snap = va.compute_initial_auth("+13555550100")
    assert snap["auth_level"] == "cid_legacy"


def test_compute_initial_auth_paid_owner(tmp_path, monkeypatch):
    path = tmp_path / "c.json"
    monkeypatch.setenv("CUSTOMERS_PATH", str(path))
    mcp = REPO_ROOT / "mcp-server"
    if str(mcp) not in sys.path:
        sys.path.insert(0, str(mcp))
    import customers

    importlib.reload(customers)
    customers.upsert("+13555550100", status="active_owner", slug="cool-cafe")
    _, _, va, _ = _reload_owner()
    snap = va.compute_initial_auth("+13555550100")
    assert snap["auth_level"] == "cid_only"


def test_anonymous_cannot_create_cr():
    _, agent, va, _ = _reload_owner()
    state = agent.CallState(
        call_sid="CA-AUTH",
        business=_Biz(),
        direction="inbound",
        caller_number="",  # no F1
    )
    state.llm = mock.Mock()
    state.auth_level = "anonymous"
    out = json.loads(agent._run_tool(state, "create_change_request", {
        "business_slug": "cool-cafe",
        "summary": "nope",
        "items": [],
    }))
    assert out.get("denied") is True or out.get("created") is False
    assert out.get("code") in ("auth_anonymous", "auth_insufficient", "not_owner")


def test_legacy_cid_can_create_unclaimed(tmp_path, monkeypatch):
    cr_path = tmp_path / "cr.jsonl"
    sites = tmp_path / "sites"
    sites.mkdir()
    (sites / "cool-cafe.html").write_text(
        "<html><head><title>Cool</title></head><body><h1>Cool</h1></body></html>",
        encoding="utf-8",
    )
    monkeypatch.setenv("CHANGE_REQUESTS_PATH", str(cr_path))
    monkeypatch.setenv("GENERATED_SITES_DIR", str(sites))
    monkeypatch.setenv("CUSTOMERS_PATH", str(tmp_path / "empty-cust.json"))
    (tmp_path / "empty-cust.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("OWNER_CR_AUTH", "soft")

    for key in list(sys.modules):
        if key in ("changerequests", "customers", "lookup", "siteedit", "mcp_bridge"):
            del sys.modules[key]

    _, agent, va, bridge = _reload_owner()
    bridge.reset_for_tests()
    state = agent.CallState(
        call_sid="CA-LEG",
        business=_Biz(),
        direction="inbound",
        caller_number="+13555550100",
    )
    state.llm = mock.Mock()
    va.apply_auth_to_state(state)
    assert state.auth_level == "cid_legacy"

    created = json.loads(
        agent._run_tool(
            state,
            "create_change_request",
            {
                "business_slug": "cool-cafe",
                "summary": "hours",
                "items": [{"type": "hours", "after": "9-5"}],
                "confirmation_spoken": True,
            },
        )
    )
    assert created.get("created") is True, created


def test_check_tool_allowed_voice_soft_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_AUTH_VENDOR", "mock")
    monkeypatch.setenv("VOICE_ENROLL_REQUIRED_FOR_WRITE", "false")
    monkeypatch.setenv("CUSTOMERS_PATH", str(tmp_path / "c.json"))
    (tmp_path / "c.json").write_text("{}\n", encoding="utf-8")
    mcp = REPO_ROOT / "mcp-server"
    if str(mcp) not in sys.path:
        sys.path.insert(0, str(mcp))
    import customers

    importlib.reload(customers)
    customers.upsert("+13555550100", status="active_owner", slug="cool-cafe")

    _, agent, va, _ = _reload_owner()
    monkeypatch.setenv("VOICE_AUTH_VENDOR", "mock")
    importlib.reload(va)

    state = agent.CallState(
        call_sid="CA-V",
        business=_Biz(),
        direction="inbound",
        caller_number="+13555550100",
        auth_level="cid_only",
    )
    # Not enrolled → speech windows skipped
    skip = va.on_speech_window(state)
    assert skip.get("reason") == "not_enrolled"

    # Enroll then promote
    en = va.enroll_owner_on_state(state, vendor="mock")
    assert en.get("ok") is True, en
    assert state.voice_enrolled is True

    deny = va.check_tool_allowed(state, "create_change_request")
    assert deny is not None
    assert deny.get("code") == "auth_voice_pending"

    for _ in range(3):
        va.on_speech_window(state)
    assert state.auth_level in ("voice_soft", "voice_hard")
    assert va.check_tool_allowed(state, "create_change_request") is None


def test_enroll_voice_tool_requires_consent_and_paid(tmp_path, monkeypatch):
    monkeypatch.setenv("CUSTOMERS_PATH", str(tmp_path / "c.json"))
    (tmp_path / "c.json").write_text("{}\n", encoding="utf-8")
    mcp = REPO_ROOT / "mcp-server"
    if str(mcp) not in sys.path:
        sys.path.insert(0, str(mcp))
    import customers

    importlib.reload(customers)
    customers.upsert("+13555550100", status="active_owner", slug="cool-cafe")
    _, agent, va, _ = _reload_owner()
    state = agent.CallState(
        call_sid="CA-E",
        business=_Biz(),
        direction="inbound",
        caller_number="+13555550100",
    )
    state.llm = mock.Mock()
    va.apply_auth_to_state(state)
    denied = json.loads(agent._run_tool(state, "enroll_voice_auth", {"consent_spoken": False}))
    assert denied.get("ok") is False
    ok = json.loads(agent._run_tool(state, "enroll_voice_auth", {"consent_spoken": True}))
    assert ok.get("ok") is True
    assert state.voice_enrolled is True


def test_read_tools_allowed_at_cid_only():
    _, agent, va, _ = _reload_owner()
    state = agent.CallState(
        call_sid="CA-R",
        business=_Biz(),
        direction="inbound",
        caller_number="+13555550100",
        auth_level="cid_only",
    )
    assert va.check_tool_allowed(state, "get_site_outline") is None
    assert va.check_tool_allowed(state, "lookup_business") is None
    assert va.check_tool_allowed(state, "end_call") is None


def test_apply_requires_step_up_then_otp(monkeypatch):
    monkeypatch.setenv("VOICE_STEP_UP_ENABLED", "true")
    monkeypatch.setenv("VOICE_STEP_UP_DEBUG_CODE", "1")
    _, agent, va, _ = _reload_owner()
    importlib.reload(va)
    state = agent.CallState(
        call_sid="CA-OTP",
        business=_Biz(),
        direction="inbound",
        caller_number="+13555550100",
        auth_level="cid_only",
        mode="owner_updates",
    )
    state.llm = mock.Mock()
    deny = va.check_tool_allowed(state, "apply_change_request")
    assert deny is not None
    assert deny.get("code") == "step_up_required"

    req = va.request_step_up_code(state, send_sms_fn=None)
    assert req.get("ok") is True
    code = req.get("debug_code")
    assert code and len(code) == 6

    bad = va.verify_step_up_code(state, "000000")
    assert bad.get("ok") is False
    good = va.verify_step_up_code(state, code)
    assert good.get("ok") is True
    assert state.step_up_ok is True
    assert va.check_tool_allowed(state, "apply_change_request") is None


def test_step_up_lockout(monkeypatch):
    monkeypatch.setenv("VOICE_STEP_UP_ENABLED", "true")
    monkeypatch.setenv("VOICE_STEP_UP_DEBUG_CODE", "1")
    monkeypatch.setenv("VOICE_STEP_UP_MAX_ATTEMPTS", "2")
    _, agent, va, _ = _reload_owner()
    importlib.reload(va)
    state = agent.CallState(
        call_sid="CA-LOCK",
        business=_Biz(),
        direction="inbound",
        caller_number="+13555550100",
        auth_level="cid_only",
    )
    va.request_step_up_code(state, send_sms_fn=None)
    va.verify_step_up_code(state, "111111")
    out = va.verify_step_up_code(state, "222222")
    # 2nd attempt may lock on exceed
    out3 = va.verify_step_up_code(state, "333333")
    assert out3.get("code") in ("locked", "mismatch", "no_pending")
    if out3.get("code") == "locked":
        assert state.auth_level == "locked"


def test_note_speech_activity_throttles(monkeypatch):
    monkeypatch.setenv("VOICE_AUTH_WINDOW_GAP_S", "10")
    _, agent, va, _ = _reload_owner()
    monkeypatch.setenv("VOICE_AUTH_VENDOR", "mock")
    importlib.reload(va)
    try:
        from voice_auth_vendors import reset_vendor_cache

        reset_vendor_cache()
    except Exception:
        pass
    state = agent.CallState(
        call_sid="CA-SP",
        business=_Biz(),
        direction="inbound",
        caller_number="+13555550100",
        mode="owner_updates",
        auth_level="cid_only",
        voice_enrolled=True,
        customer={
            "phone": "+13555550100",
            "status": "active_owner",
            "voice_auth": {"enrolled_at": "2026-08-14", "template_id": "t1"},
        },
    )
    a = va.note_speech_activity(state, force=True)
    assert a.get("reason") != "vendor_none", a
    b = va.note_speech_activity(state, force=False)
    assert b.get("reason") == "throttled"


def test_dormancy_requires_step_up_for_create(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_AUTH_DORMANCY_DAYS", "30")
    monkeypatch.setenv("VOICE_STEP_UP_ENABLED", "true")
    path = tmp_path / "c.json"
    monkeypatch.setenv("CUSTOMERS_PATH", str(path))
    mcp = REPO_ROOT / "mcp-server"
    if str(mcp) not in sys.path:
        sys.path.insert(0, str(mcp))
    import customers

    importlib.reload(customers)
    customers.upsert("+13555550100", status="active_owner", slug="cool-cafe")
    customers.mark_voice_enrolled("+13555550100", vendor="mock")
    # last call 100 days ago
    customers.upsert(
        "+13555550100",
        patch={
            "voice_auth": {
                **(customers.get("+13555550100") or {}).get("voice_auth", {}),
                "last_call_at": "2025-01-01T00:00:00+00:00",
            }
        },
    )
    _, agent, va, _ = _reload_owner()
    importlib.reload(va)
    snap = va.compute_initial_auth("+13555550100")
    assert "dormant" in snap["anomaly"]["flags"]
    assert snap["require_step_up"] is True
    state = agent.CallState(
        call_sid="CA-DORM",
        business=_Biz(),
        direction="inbound",
        caller_number="+13555550100",
        mode="owner_updates",
    )
    va.apply_auth_to_state(state)
    deny = va.check_tool_allowed(state, "create_change_request")
    assert deny and deny.get("code") == "step_up_required"
    assert "dormant" in (deny.get("anomaly_flags") or [])


def test_template_aged_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_AUTH_TEMPLATE_MAX_AGE_DAYS", "10")
    path = tmp_path / "c.json"
    monkeypatch.setenv("CUSTOMERS_PATH", str(path))
    mcp = REPO_ROOT / "mcp-server"
    if str(mcp) not in sys.path:
        sys.path.insert(0, str(mcp))
    import customers

    importlib.reload(customers)
    customers.upsert("+13555550100", status="active_owner", slug="cool-cafe")
    customers.upsert(
        "+13555550100",
        patch={
            "voice_auth": {
                "enrolled_at": "2020-01-01T00:00:00+00:00",
                "template_id": "old",
                "fail_streak": 0,
            }
        },
    )
    _, _, va, _ = _reload_owner()
    importlib.reload(va)
    flags = va.analyze_anomaly_flags(customers.get("+13555550100"), "+13555550100")
    assert "template_aged" in flags["flags"]


def test_replay_pcm_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_AUTH_VENDOR", "mock")
    path = tmp_path / "c.json"
    monkeypatch.setenv("CUSTOMERS_PATH", str(path))
    mcp = REPO_ROOT / "mcp-server"
    if str(mcp) not in sys.path:
        sys.path.insert(0, str(mcp))
    import customers

    importlib.reload(customers)
    customers.upsert("+13555550100", status="active_owner", slug="cool-cafe")
    customers.mark_voice_enrolled("+13555550100", vendor="mock", template_id="t-replay")
    _, agent, va, _ = _reload_owner()
    monkeypatch.setenv("VOICE_AUTH_VENDOR", "mock")
    importlib.reload(va)
    try:
        from voice_auth_vendors import reset_vendor_cache

        reset_vendor_cache()
    except Exception:
        pass
    state = agent.CallState(
        call_sid="CA-RP",
        business=_Biz(),
        direction="inbound",
        caller_number="+13555550100",
        mode="owner_updates",
        auth_level="cid_only",
    )
    va.apply_auth_to_state(state)
    pcm = b"\x00\x01\x02\x03" * 20
    r1 = va.on_speech_window(state, pcm=pcm)
    assert r1.get("ok") is True
    r2 = va.on_speech_window(state, pcm=pcm)
    assert r2.get("code") == "replay" or r2.get("error") == "replay_detected"


def test_new_ani_flag(tmp_path, monkeypatch):
    path = tmp_path / "c.json"
    monkeypatch.setenv("CUSTOMERS_PATH", str(path))
    mcp = REPO_ROOT / "mcp-server"
    if str(mcp) not in sys.path:
        sys.path.insert(0, str(mcp))
    import customers

    importlib.reload(customers)
    customers.upsert(
        "+13555550100",
        status="active_owner",
        slug="cool-cafe",
        patch={"trusted_phones": ["+13555550100", "+13555550999"]},
    )
    customers.upsert(
        "+13555550100",
        patch={
            "voice_auth": {
                "last_ani": "+13555550100",
                "last_call_at": "2026-08-01T00:00:00+00:00",
            }
        },
    )
    # call from trusted delegate line
    _, _, va, _ = _reload_owner()
    importlib.reload(va)
    # find_customers_for_phone on 999 must find the row
    flags = va.analyze_anomaly_flags(customers.get("+13555550100"), "+13555550999")
    assert "new_ani" in flags["flags"]


