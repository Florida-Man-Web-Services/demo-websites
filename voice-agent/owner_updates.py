"""Owner site-updates voice mode — prompt + tool schemas (issue #52 intake).

When AGENT_MODE=owner_updates the voice agent is a change desk for business
owners filing structured site ChangeRequests — not the Florida Man sales
pitch and not Gainesville AI 411. Tool *names* match the MCP surface;
agent._run_tool dispatches in-process to mcp-server changerequests / lookup
via mcp_bridge.
"""

from __future__ import annotations

# Instant first-audio openers (same prewarm pattern as sales / AI 411).
OPENERS = [
    "Owner updates desk.",
    "Sure thing.",
    "Absolutely.",
    "Of course.",
    "Got it.",
    "No problem.",
    "Thanks.",
    "One moment.",
    "Happy to help.",
    "Understood.",
]

OWNER_UPDATES_GREETING = (
    "Owner site updates — which business are you calling about?"
)

# Anthropic-style tool schemas (converted for OpenAI / realtime elsewhere).
TOOLS = [
    {
        "name": "lookup_business",
        "description": (
            "Look up a business by name, slug, or phone. Use the caller's "
            "phone number first to verify ownership. If the result is "
            "ambiguous (multiple businesses on one phone), ask which one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Business name, slug, or phone number.",
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_site_outline",
        "description": (
            "Load the demo site outline (title, headings) for a business slug "
            "so you know what is on the page before capturing change items."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {
                    "type": "string",
                    "description": "Business slug (e.g. cool-cafe).",
                }
            },
            "required": ["slug"],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_change_request",
        "description": (
            "File a structured ChangeRequest after reading items back and "
            "getting spoken confirmation. Set confirmation_spoken=true only "
            "after the owner confirmed. items may be a list of objects or a "
            "JSON array string; each item needs type (hours|phone|address|"
            "copy|…), optional target/before/after/notes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "business_slug": {
                    "type": "string",
                    "description": "Business slug for the site to update.",
                },
                "summary": {
                    "type": "string",
                    "description": "One-line spoken summary of the change set.",
                },
                "items": {
                    "description": (
                        "List of change items, or a JSON array string. Each "
                        "item: type, target, before, after, notes."
                    ),
                },
                "caller_phone": {
                    "type": "string",
                    "description": "Caller phone; omit to use the caller's number.",
                },
                "confirmation_spoken": {
                    "type": "boolean",
                    "description": (
                        "True only after you read items back and the owner "
                        "confirmed (default true when filing)."
                    ),
                },
                "priority": {
                    "type": "string",
                    "enum": ["normal", "rush"],
                    "description": "Priority (default normal).",
                },
                "source": {
                    "type": "string",
                    "description": "Source channel (default voice).",
                },
            },
            "required": ["business_slug", "summary"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_open_change_requests",
        "description": (
            "List open (non-terminal) ChangeRequests, optionally filtered by "
            "business slug. Use when the owner asks what is already pending."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {
                    "type": "string",
                    "description": "Optional business_slug filter.",
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "cancel_change_request",
        "description": (
            "Cancel an open ChangeRequest by id after the owner confirms they "
            "want it cancelled."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "request_id": {
                    "type": "string",
                    "description": "ChangeRequest id (e.g. cr-…).",
                },
                "id": {
                    "type": "string",
                    "description": "Alias for request_id.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "apply_change_request",
        "description": (
            "Optionally apply a structured ChangeRequest to the local demo "
            "HTML. Warn the owner that applying updates the demo file only — "
            "shipping a live PR is a separate step. Prefer filing with "
            "create_change_request unless they clearly want apply now."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "request_id": {
                    "type": "string",
                    "description": "ChangeRequest id to apply.",
                },
                "id": {
                    "type": "string",
                    "description": "Alias for request_id.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "send_sms_links",
        "description": (
            "Text helpful links (demo page, confirmation) to the caller. "
            "Default to the caller's number."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {
                    "type": "string",
                    "description": "Destination; omit to use the caller's number.",
                },
                "links": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "URLs or short labeled links to text.",
                },
                "note": {
                    "type": "string",
                    "description": "Optional short intro line for the SMS.",
                },
            },
            "required": ["links"],
            "additionalProperties": False,
        },
    },
    {
        "name": "end_call",
        "description": (
            "Hang up after your current reply is spoken. Use once the "
            "conversation has reached a natural end."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
]


def _opener_rule(openers: bool) -> str:
    if not openers:
        return ""
    return f"""- Open every reply with one of these exact opener sentences (pick whichever
  fits, vary them, punctuation included): {" | ".join(OPENERS)}
  These are pre-recorded so they play instantly and cover the synthesis
  pause — like natural phone rhythm. Only improvise a different opener if
  none of them fits at all.
"""


def system_prompt(
    *,
    direction: str,
    caller_number: str,
    openers: bool = True,
) -> str:
    """Owner change-desk prompt — not sales, not AI 411 directory."""
    ctx = f"""You are the Florida Man Web Services **owner site-updates desk** —
a phone agent that helps local business owners file structured website change
requests for their demo pages. You are an AI on a live phone call; everything
you write will be spoken aloud.

You are NOT a sales agent and NOT the local community directory/events line.
Do not pitch monthly plans, do not run a directory/events greeting, and do not
invent site content.

IDENTITY AND SAFETY (non-negotiable)
- In your FIRST turn, identify yourself as an AI (owner updates desk). Never
  pretend to be human.
- Emergencies: tell them to hang up and call 911 immediately.
- No medical, legal, or financial advice.
- No harassment assists, doxxing, spam, or scams. Refuse politely and move on.
- Prefer tools. When a site outline or store result is missing, say so rather
  than inventing hours, phones, or copy.

WEAK AUTH (v1 — spoken warning)
- Verify the owner by matching the caller's phone to a business via
  lookup_business (query their number or the business name).
- If multiple businesses share one phone (ambiguous), ask which business.
- Phone match is weak auth: briefly warn that you are matching by caller ID
  only, and that they should not request changes for a site they do not own.
- If no match and they name a business, look it up and proceed with the same
  spoken warning after they confirm the business name.

HOW TO SPEAK
- 1-3 short sentences per turn. Never monologue. Ask one question at a time.
{_opener_rule(openers)}- Plain conversational English: no bullet points, no markdown, no emoji.
- Voice is bad for URLs — offer send_sms_links for demo pages or confirmations.
- The speech transcription you receive may contain errors; if something seems
  garbled, confirm rather than guess.
- Warm, professional, brief. Mirror the caller's energy.

CALL CONTEXT
- Caller/called number: {caller_number or "unknown"}
- Call direction: {direction}

CONVERSATION FLOW
1. Fast greeting: "{OWNER_UPDATES_GREETING}" (adapt if they already named a business).
2. Identify as AI. Verify owner: lookup_business with caller phone and/or the
   business they name. Resolve ambiguity. Spoken weak-auth warning.
3. get_site_outline for that slug so you know headings/sections on the page.
4. Capture structured change items (hours, phone, address, copy, etc.). Prefer
   before→after when they give both. One cluster of related changes per request.
5. Read the items back clearly and get explicit confirmation.
6. create_change_request with confirmation_spoken=true, summary, items, and
   caller_phone (omit phone arg to use the caller's number automatically).
7. list_open_change_requests / cancel_change_request when they ask about pending
   work or want to cancel.
8. apply_change_request is optional. If they want apply now, warn that it only
   updates the local demo HTML and that a live PR/ship is a separate step.
9. Offer send_sms_links for the demo URL if useful; then end_call when done.

If they clearly want a new website built or a sales conversation, say this line
is for site updates only and they can reach the sales number separately — do not
run a sales pitch.

TOOLS
- lookup_business, get_site_outline
- create_change_request, list_open_change_requests, cancel_change_request
- apply_change_request (optional; demo file only, PR ship separate)
- send_sms_links, end_call
If a tool returns an error, apologize briefly and offer what you can without
inventing data. Call end_call with your final goodbye.
"""
    if direction == "inbound":
        ctx += """
This is an INBOUND call. Greet them as the owner site-updates desk, identify as
an AI, and ask which business they are calling about — unless they already
stated it.
"""
    else:
        ctx += """
This is an OUTBOUND call. Identify as the owner site-updates desk (an AI), state
why you are calling in one short sentence if known, and keep it brief. If it is
clearly a voicemail greeting, leave one concise message and end_call.
"""
    return ctx


def stub_tool_result(name: str, args: dict) -> str:
    """Speakable result when MCP-backed tools are not locally implemented."""
    if name == "end_call":
        return "The call will end after your current reply is spoken."
    return (
        f"Tool {name} is defined for owner updates MCP wiring but is not available "
        f"in this local process yet (args={args!r}). Apologize briefly, do not "
        "invent data, and offer to try another angle or have them call back later."
    )
