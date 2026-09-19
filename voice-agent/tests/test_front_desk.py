"""Front-desk voice contract tests."""

from __future__ import annotations

import front_desk
import front_desk_privacy as privacy


def test_greeting_and_tools():
    prompt = front_desk.system_prompt(business_name="Cool Cafe", slug="cool-cafe", release_id="rel-1")
    assert "Cool Cafe" in prompt
    assert "automated receptionist" in prompt
    assert "911" in prompt
    assert "live transfer" in prompt
    names = {t["name"] for t in front_desk.TOOLS}
    assert names == {
        "front_desk_get_business",
        "front_desk_leave_message",
        "front_desk_request_appointment",
        "front_desk_request_owner_callback",
    }
    assert "create_change_request" not in names
    assert "apply_change_request" not in names


def test_prompt_does_not_elevate_owner_cid():
    prompt = front_desk.system_prompt(business_name="Cool Cafe", slug="cool-cafe", release_id="rel-1")
    assert "caller ID" not in prompt.lower() or "not" in prompt.lower()
    assert "cannot edit the website" in prompt


def test_privacy_redacts_phone_otp_and_email():
    text = "Call me at +1 (352) 555-0100 code 123456 email owner@example.test"
    out = privacy.sanitize_for_llm(text)
    assert "+1" not in out
    assert "555-0100" not in out
    assert "123456" not in out
    assert "owner@example.test" not in out
    assert "[redacted]" in out


def test_filter_drops_contact_ref():
    filtered = privacy.filter_tool_response({"ok": True, "contact_ref": "opaque", "message": "hi 3525550100"})
    assert "contact_ref" not in filtered
    assert "3525550100" not in filtered["message"]
