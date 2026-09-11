"""AI411 prompt: direct intent, zero filler, no Dial."""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _reload():
    os.environ["AGENT_MODE"] = "ai411"
    import config
    import ai411

    importlib.reload(config)
    importlib.reload(ai411)
    return ai411


def test_greeting_is_an_invite():
    ai411 = _reload()
    assert "Gainesville" in ai411.AI411_GREETING
    assert "need" in ai411.AI411_GREETING.lower()
    assert ai411.AI411_GREETING == ai411.OPENERS[0]


def test_openers_have_no_filler():
    ai411 = _reload()
    banned = (
        "good question",
        "great question",
        "sure thing",
        "absolutely",
        "happy to help",
        "of course",
        "one moment",
    )
    blob = " ".join(ai411.OPENERS).lower()
    for b in banned:
        assert b not in blob, b


def test_explicit_date_night_skips_interest_gate():
    ai411 = _reload()
    p = ai411.system_prompt(
        direction="inbound", caller_number="+135****0100", openers=False
    )
    assert "date_night" in p.lower() or "good date" in p.lower()
    assert "Do NOT call search_events" not in p or "explicit" in p.lower()
    pl = p.lower()
    assert "i can't book" in pl or "cannot book" in pl or "not a booking" in pl
    # Empty browse may still wait; explicit date/movies/Hipp must not.
    assert "IMMEDIATELY" in p or "immediately" in pl
    # QOTD is optional after help — never a gate on explicit date/movies/Hipp.
    assert "question of the day" in pl
    assert "get_question_of_the_day" in p
    assert "never" in pl and "qotd" in pl


def test_qotd_does_not_run_on_silence():
    ai411 = _reload()
    p = ai411.system_prompt(
        direction="inbound", caller_number="+13555550100", openers=False
    )
    pl = p.lower()
    assert "do not launch question of the day on silence" in pl
    assert "events, food, or a number" in pl
    assert "get_question_of_the_day" in p


def test_connect_is_spoken_number_not_dial():
    ai411 = _reload()
    p = ai411.system_prompt(
        direction="inbound", caller_number="+135****0100", openers=False
    )
    pl = p.lower()
    assert "spoken" in pl or "give you their number" in pl
    assert "<Dial>" not in p
    names = {t["name"] for t in ai411.TOOLS}
    assert "log_connect" in names


def test_fmws_facts_are_gated_and_honest():
    ai411 = _reload()
    p = ai411.system_prompt(
        direction="inbound", caller_number="+1" + "352" + "555" + "0100", openers=False
    )
    assert ai411.AI411_GREETING == "A411 in Gainesville. What do you need?"
    assert "FMWS FACTS" in p
    assert "https://arete.floridamanweb.online/" in p
    assert "not an official uf service" in p.lower()
    assert "waitlist" in p.lower()
    assert "ONLY if they" in p
    assert "never concatenated" in p.lower() or "never recap a product menu" in p.lower()
    assert "directory lookup, not FMWS" in p
    assert "resume_waitlist) never triggers" in p or "never triggers FMWS facts" in p
    assert "$999" not in p
    assert "Arete Holdings" in p  # ban list only
    names = {t["name"] for t in ai411.TOOLS}
    assert "search_business_knowledge" in names
    assert "get_fmws_facts" not in names


def test_fmws_facts_do_not_add_tools():
    ai411 = _reload()
    names = [t["name"] for t in ai411.TOOLS]
    assert len(names) == len(set(names))
    assert "search_business_knowledge" in names
    assert not any("fmws" in n.lower() for n in names)


def test_search_events_schema_has_source_and_kind():
    ai411 = _reload()
    tool = next(t for t in ai411.TOOLS if t["name"] == "search_events")
    props = tool["input_schema"]["properties"]
    assert "source" in props
    assert "kind" in props


def test_cinema_miss_kind_on_log_connect():
    ai411 = _reload()
    tool = next(t for t in ai411.TOOLS if t["name"] == "log_connect")
    kind = tool["input_schema"]["properties"]["kind"]["description"].lower()
    assert "cinema_miss" in kind
    p = ai411.system_prompt(
        direction="inbound", caller_number="+1" + "352" + "555" + "0100", openers=False
    )
    pl = p.lower()
    assert "cinema_miss" in pl or "tonight's board" in pl or "tonights board" in pl
