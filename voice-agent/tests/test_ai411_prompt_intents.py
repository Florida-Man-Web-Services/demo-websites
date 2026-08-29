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


def test_greeting_unchanged():
    ai411 = _reload()
    assert ai411.AI411_GREETING == "A411 here."


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
    # QOTD stays in the product: after the answer, not as a gate.
    assert "question of the day" in pl
    assert "get_question_of_the_day" in p
    assert "do not drop qotd" in pl or "offer today's qotd" in pl or "offer qotd" in pl


def test_qotd_is_default_on_empty_or_bored():
    ai411 = _reload()
    p = ai411.system_prompt(
        direction="inbound", caller_number="+135****0100", openers=False
    )
    pl = p.lower()
    assert "bored" in pl
    assert "silence after greeting" in pl or "default people-profile" in pl
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
