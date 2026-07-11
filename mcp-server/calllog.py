"""Append-only call-outcome logging + history reads for the MCP server.

Shares the CSV schema and outcome enum with voice-agent/agent.py, but each
deployment writes its own file: the Twilio agent logs to
voice-agent/call-log.csv locally, while this MCP server logs to
/data/call-log.csv on its PVC. The logs are not merged or synchronized —
get_call_history (and history_for below) only covers calls logged through
this server.
"""

import csv
import threading
import uuid
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
    business,
    outcome: str,
    notes: str,
    email: str = "",
    callback_time: str = "",
    caller_phone: str = "",
    direction: str = "",
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
                f"XAI-{now.strftime('%Y%m%dT%H%M%S')}-{business.slug}-{uuid.uuid4().hex[:8]}",
                direction,
                business.name,
                business.slug,
                caller_phone or business.phone,
                outcome,
                email,
                callback_time,
                notes,
            ])
    return {"logged": True}
