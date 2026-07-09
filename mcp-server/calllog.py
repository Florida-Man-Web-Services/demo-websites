"""Append-only call-outcome logging + history reads for the MCP server.

Shares the CSV schema and outcome enum with voice-agent/agent.py so the
Twilio agent and the xAI agent write to interchangeable logs.
"""

import csv
import threading
from datetime import datetime

import config

# Must stay in sync with the log_call_outcome enum in voice-agent/agent.py.
VALID_OUTCOMES = [
    "interested",
    "wants_email",
    "callback_requested",
    "sent_sms",
    "not_interested",
    "do_not_call",
    "wrong_number",
    "voicemail",
    "other",
]

COLUMNS = [
    "timestamp", "call_sid", "direction", "business", "slug",
    "phone", "outcome", "email", "callback_time", "notes",
]

_write_lock = threading.Lock()


def history_for(slug: str) -> list[dict]:
    if not config.CALL_LOG.exists():
        return []
    with open(config.CALL_LOG, newline="", encoding="utf-8") as f:
        return [row for row in csv.DictReader(f) if row.get("slug") == slug]


def append_outcome(
    business, outcome: str, notes: str, email: str = "", callback_time: str = ""
) -> dict:
    if outcome not in VALID_OUTCOMES:
        return {
            "logged": False,
            "error": f"invalid outcome {outcome!r}",
            "valid_outcomes": VALID_OUTCOMES,
        }
    now = datetime.now()
    with _write_lock:
        is_new = not config.CALL_LOG.exists()
        with open(config.CALL_LOG, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if is_new:
                writer.writerow(COLUMNS)
            writer.writerow([
                now.isoformat(timespec="seconds"),
                f"XAI-{now.strftime('%Y%m%dT%H%M%S')}-{business.slug}",
                "xai",
                business.name,
                business.slug,
                business.phone,
                outcome,
                email,
                callback_time,
                notes,
            ])
    return {"logged": True}
