"""Append-only call-outcome logging + history reads for the MCP server.

Shares schema/enum with voice-agent. Durable storage is the relational SQLite
call DB (`config.CALL_DB`: tables `calls`, `transcripts`, `transcript_turns`).
CSV dual-write remains for ops greps and older tooling.

Voice-agent and this MCP process each have their own files unless CALL_DB /
CALL_LOG are pointed at a shared volume — they are not auto-merged.
"""

import threading
import uuid
from datetime import datetime

import config
import calldb

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
    # Owner site-updates desk (voice AGENT_MODE=owner_updates / unified)
    "owner_update_filed",
    "owner_update_cancelled",
    "owner_update_applied",
    "no_change",
]

COLUMNS = [
    "timestamp", "call_sid", "direction", "business", "slug",
    "phone", "outcome", "email", "callback_time", "notes",
]

_write_lock = threading.Lock()


def history_for(slug: str) -> list[dict]:
    """Prior outcomes for a business. Prefers SQLite; falls back to CSV."""
    if calldb.enabled():
        rows = calldb.history_for(slug)
        if rows:
            return rows
        # Empty DB may still have legacy CSV-only rows before migrate runs.
    # CSV fallback (and empty-DB path after migrate attempt)
    calldb.init_db()
    if calldb.enabled():
        rows = calldb.history_for(slug)
        if rows:
            return rows
    if not config.CALL_LOG.exists():
        return []
    import csv

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
    call_sid: str = "",
) -> dict:
    if outcome not in VALID_OUTCOMES:
        return {
            "logged": False,
            "error": f"invalid outcome {outcome!r}",
            "valid_outcomes": VALID_OUTCOMES,
        }
    now = datetime.now()
    sid = call_sid or (
        f"XAI-{now.strftime('%Y%m%dT%H%M%S')}-{business.slug}-{uuid.uuid4().hex[:8]}"
    )
    with _write_lock:
        dual = getattr(config, "CALL_LOG_DUAL_WRITE_CSV", True)
        result = calldb.log_outcome(
            call_sid=sid,
            direction=direction or "",
            business=business.name,
            slug=business.slug,
            phone=caller_phone or business.phone or "",
            outcome=outcome,
            email=email or "",
            callback_time=callback_time or "",
            notes=notes or "",
            source="mcp",
            dual_write_csv=dual,
        )
    out = {"logged": True, "call_sid": sid}
    if result.get("transcript_ref"):
        out["transcript_ref"] = result["transcript_ref"]
    return out
