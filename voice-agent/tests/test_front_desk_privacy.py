"""Privacy boundary extras: incidental phones in free text."""

from __future__ import annotations

import front_desk_privacy as privacy


def test_incidental_phone_in_faq_text():
    leaked = privacy.sanitize_for_llm("Our old flyer said 352.555.0199 but ignore that.")
    assert "555" not in leaked
    assert "[redacted]" in leaked
