"""Strip phones, emails, OTPs, and capability-looking tokens before LLM context."""

from __future__ import annotations

import re
from typing import Any

_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d().\-\s]{6,}\d)(?!\w)")
_EMAIL = re.compile(r"(?<!\w)[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+")
_OTP = re.compile(r"(?<!\w)\d{4,8}(?!\w)")
_CAPABILITY = re.compile(r"\b(?:eyJ[A-Za-z0-9_-]{8,}|[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,})\b")


def sanitize_for_llm(text: str | None) -> str:
    if not text:
        return ""
    out = _PHONE.sub("[redacted]", text)
    out = _EMAIL.sub("[redacted]", out)
    out = _CAPABILITY.sub("[redacted]", out)
    out = _OTP.sub("[redacted]", out)
    return out


def filter_tool_response(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_for_llm(value)
    if isinstance(value, dict):
        blocked = {"contact_ref", "phone", "otp", "token", "cookie", "authorization"}
        return {k: filter_tool_response(v) for k, v in value.items() if k not in blocked}
    if isinstance(value, list):
        return [filter_tool_response(v) for v in value]
    return value
