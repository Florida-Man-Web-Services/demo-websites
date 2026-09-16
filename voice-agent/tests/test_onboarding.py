"""Minimum viable onboarding brief and progressive-disclosure contract."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MCP = ROOT.parent / "mcp-server"
if str(MCP) not in sys.path:
    sys.path.insert(0, str(MCP))

import onboarding


def test_mvp_fields_are_small_and_ordered():
    assert onboarding.MVP_REQUIRED_FIELDS == (
        "business_name",
        "audience",
        "goal",
        "must_haves",
        "follow_up",
    )
    assert set(onboarding.MVP_REQUIRED_FIELDS).isdisjoint(onboarding.OPTIONAL_FIELDS)


def test_missing_mvp_fields_accept_legacy_aliases():
    requirements = {
        "business": "North Star Books",
        "audience": "Readers looking for used books",
        "goals": ["bring people into the shop"],
        "must_haves": ["hours", "contact details"],
        "next_step": "Text the demo when it is ready",
    }
    assert onboarding.missing_mvp_fields(requirements) == ()
    assert onboarding.mvp_brief_complete(requirements) is True


def test_signup_fields_count_as_business_but_not_other_answers():
    requirements = onboarding.requirements_for_customer(
        {
            "business_name": "North Star Books",
            "email": "owner@example.test",
            "requirements": {"audience": "Local readers"},
        }
    )
    assert requirements["business_name"] == "North Star Books"
    assert "email" not in onboarding.missing_mvp_fields(requirements)
    assert onboarding.missing_mvp_fields(requirements) == (
        "goal",
        "must_haves",
        "follow_up",
    )


def test_finalize_schema_requires_explicit_confirmation():
    tool = next(t for t in onboarding.TOOLS if t["name"] == "finalize_requirements")
    assert tool["input_schema"]["required"] == [
        "summary",
        "requirements",
        "confirmation_spoken",
    ]


def test_prompt_requires_mvp_before_optional_discovery():
    prompt = onboarding.system_prompt(
        direction="outbound",
        caller_number="+13555550100",
        openers=False,
    )
    flow = prompt[prompt.index("INTERVIEW FLOW") :]
    positions = [flow.index(label) for label in ("BUSINESS", "AUDIENCE", "GOAL", "MUST-HAVES", "FOLLOW-UP")]
    assert positions == sorted(positions)
    assert flow.index("all five MVP fields") < flow.index("optional details")
    assert "Do not ask optional discovery questions until all five MVP fields" in flow
    assert "Wait for a successful" in flow
    assert "$999" not in prompt
    assert "hard sell" in prompt


def test_resumed_prompt_shows_only_remaining_mvp_fields():
    prompt = onboarding.system_prompt(
        direction="inbound",
        caller_number="+13555550100",
        openers=False,
        customer={
            "business_name": "North Star Books",
            "requirements": {
                "audience": "Local readers",
                "goal": "Bring people into the shop",
            },
        },
    )
    assert "MVP fields still needed: must_haves, follow_up" in prompt
    assert "Do not re-ask an MVP field" in prompt


def test_first_turn_identifies_free_demo_without_sales_pitch():
    assert "free demo website" in onboarding.ONBOARDING_GREETING
    assert "$999" not in onboarding.ONBOARDING_GREETING


@pytest.fixture()
def onboarding_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "onboarding")
    monkeypatch.setenv("CUSTOMERS_PATH", str(tmp_path / "customers.json"))
    monkeypatch.setenv("MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("CALL_DB", "0")
    import config
    import customers
    import agent

    importlib.reload(config)
    importlib.reload(customers)
    importlib.reload(agent)
    return agent, customers


def test_queue_requires_a_finalized_complete_brief(onboarding_agent):
    agent, customers = onboarding_agent
    phone = "+13555550100"
    customers.register_callback(phone, business_name="North Star Books")
    state = SimpleNamespace(caller_number=phone, customer={})

    blocked = json.loads(agent._run_onboarding_tool(state, "queue_website_build", {}))
    assert blocked["ok"] is False
    assert "finalize_requirements" in blocked["error"]

    brief = {
        "business_name": "North Star Books",
        "audience": "Local readers",
        "goal": "Bring people into the shop",
        "must_haves": ["hours", "contact"],
        "follow_up": "Text the demo",
    }
    finalized = json.loads(
        agent._run_onboarding_tool(
            state,
            "finalize_requirements",
            {"summary": "A local bookstore site.", "requirements": brief},
        )
    )
    assert finalized["ok"] is False
    assert "confirmation_spoken" in finalized["error"]

    finalized = json.loads(
        agent._run_onboarding_tool(
            state,
            "finalize_requirements",
            {
                "summary": "A local bookstore site.",
                "requirements": brief,
                "confirmation_spoken": True,
            },
        )
    )
    assert finalized["ok"] is True
    queued = json.loads(agent._run_onboarding_tool(state, "queue_website_build", {}))
    assert queued["ok"] is True
    assert customers.get(phone)["status"] == "building"
