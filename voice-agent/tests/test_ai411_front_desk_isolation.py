"""Front-desk tools vs public AI411 isolation."""

from __future__ import annotations

import agent
import ai411
import front_desk


def test_front_desk_tools_are_not_on_ai411():
    ai411_names = {t["name"] for t in ai411.TOOLS}
    desk_names = {t["name"] for t in front_desk.TOOLS}
    assert desk_names.isdisjoint(ai411_names)
    assert "search_business_knowledge" not in desk_names
    assert "front_desk_get_business" not in ai411_names


def test_get_tools_front_desk_mode():
    names = {t["name"] for t in agent.get_tools("front_desk")}
    assert names == {t["name"] for t in front_desk.TOOLS}
    ai411_names = {t["name"] for t in agent.get_tools("ai411")}
    assert "front_desk_leave_message" not in ai411_names
