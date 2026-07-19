"""Gainesville AI 411 voice mode — prompt + tool schemas (issue #51).

When AGENT_MODE=ai411 the voice agent is a local directory/events operator,
not the Florida Man Web Services sales pitch. Tool *names* match the MCP
surface; agent._run_tool dispatches in-process to mcp-server stores via
mcp_bridge (knowledge / events / callers / broadcasts / lookup).
"""

from __future__ import annotations

# Instant first-audio openers (same prewarm pattern as sales OPENERS).
OPENERS = [
    "Gainesville AI 411.",
    "Sure thing.",
    "Absolutely.",
    "Of course.",
    "Good question.",
    "No problem.",
    "Got it.",
    "Thanks.",
    "Happy to help.",
    "One moment.",
]

AI411_GREETING = (
    "Gainesville AI 411 — events, businesses, or post something?"
)

# Anthropic-style tool schemas (converted for OpenAI / realtime elsewhere).
TOOLS = [
    {
        "name": "search_business_knowledge",
        "description": (
            "Search local cached knowledge about Gainesville businesses "
            "(demo-site text, hours language, services). Prefer this when the "
            "caller asks what's on a page or needs more than a short profile."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What the caller is asking about.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max snippets to return (default 5).",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lookup_business",
        "description": (
            "Look up a Gainesville business by name, slug, or phone. Returns "
            "profile fields and demo URL when known, or close-name suggestions."
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
        "name": "search_events",
        "description": (
            "Search cached local events/calendars (what's on this weekend, "
            "free outdoor events, etc.). Seed data is always available."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Event search query or topic (may be empty).",
                },
                "when": {
                    "type": "string",
                    "description": (
                        "Optional time window: tonight, tomorrow, "
                        "this_weekend, or empty for all upcoming."
                    ),
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags filter (e.g. music, free, outdoor).",
                },
                "free_only": {
                    "type": "boolean",
                    "description": "If true, only free events (default false).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max events to return (default 5).",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_event",
        "description": "Load details for one event by id from the events cache.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "Event identifier from search_events.",
                }
            },
            "required": ["event_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_caller_profile",
        "description": (
            "Load this caller's remembered profile by phone (when they have "
            "consented to memory). Use early on return callers to personalize."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {
                    "type": "string",
                    "description": "E.164 or US 10-digit; omit to use caller's number.",
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "update_caller_profile",
        "description": (
            "Create or merge-patch caller profile fields: preferred name, "
            "interests, areas, consent flags, last topics. Only with consent."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {
                    "type": "string",
                    "description": "Caller phone; omit to use caller's number.",
                },
                "patch": {
                    "type": "object",
                    "description": (
                        "Fields to merge (display_name, preferred_name, "
                        "preferences, consent, last_topics, etc.)."
                    ),
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "forget_caller",
        "description": (
            "Hard-delete the caller's profile (\"forget me\"). Idempotent. "
            "Confirm briefly, then call this when they ask to be forgotten."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {
                    "type": "string",
                    "description": "Caller phone; omit to use caller's number.",
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "submit_event_broadcast",
        "description": (
            "Submit a moderated community event broadcast (title, when, where, "
            "summary). Reject harassment, spam, illegal content, or medical/legal "
            "advice posts. Confirm details with the caller first. when_start "
            "should be ISO datetime when possible."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "when": {
                    "type": "string",
                    "description": "Date/time description or ISO when_start.",
                },
                "when_start": {
                    "type": "string",
                    "description": "ISO datetime start (preferred over when).",
                },
                "when_end": {
                    "type": "string",
                    "description": "Optional ISO datetime end.",
                },
                "where": {"type": "string", "description": "Venue or area."},
                "venue": {"type": "string", "description": "Venue (alias for where)."},
                "summary": {"type": "string"},
                "text": {"type": "string", "description": "Event description (alias for summary)."},
                "free": {"type": "boolean", "description": "Whether the event is free."},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "url": {"type": "string"},
                "phone": {
                    "type": "string",
                    "description": "Author phone; omit to use caller's number.",
                },
                "contact": {
                    "type": "string",
                    "description": "Optional contact for the event.",
                },
            },
            "required": ["title"],
            "additionalProperties": False,
        },
    },
    {
        "name": "submit_notice_broadcast",
        "description": (
            "Submit a short community notice (≤280 chars). Categories: tips, "
            "music, food, traffic, general. Same policy rules as event broadcasts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Notice text."},
                "text": {"type": "string", "description": "Notice text (alias for summary)."},
                "category": {
                    "type": "string",
                    "enum": ["tips", "music", "food", "traffic", "general"],
                    "description": "Notice category (default general).",
                },
                "area": {
                    "type": "string",
                    "description": "Optional neighborhood; folded into text if not a category.",
                },
                "phone": {
                    "type": "string",
                    "description": "Author phone; omit to use caller's number.",
                },
                "expires_at": {
                    "type": "string",
                    "description": "Optional ISO expiry; default ~14 days.",
                },
            },
            "required": ["summary"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_recent_broadcasts",
        "description": "List recent approved community broadcasts (events/notices).",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max items (default 5).",
                },
                "kind": {
                    "type": "string",
                    "enum": ["event", "notice", "all"],
                    "description": "Filter by broadcast kind (default all).",
                },
                "category": {
                    "type": "string",
                    "description": (
                        "Filter: empty/all, 'event', or notice category "
                        "(tips|music|food|traffic|general)."
                    ),
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "send_sms_links",
        "description": (
            "Text helpful result links (business/event pages) to the caller — "
            "not a sales pitch. Use after listing 2–3 spoken results when URLs "
            "would help. Default to the caller's number."
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
    """AI 411 operator prompt — no Florida Man $999 sales pitch."""
    ctx = f"""You are Gainesville AI 411 — a helpful local phone operator for the
Gainesville, Florida community. Callers dial you for events, local businesses,
community notices, and light personalization by phone. You are an AI on a live
phone call; everything you write will be spoken aloud.

IDENTITY AND SAFETY (non-negotiable)
- In your FIRST turn, identify yourself as an AI (Gainesville AI 411). Never
  pretend to be human.
- Emergencies: tell them to hang up and call 911 immediately. Do not try to
  handle medical, police, or fire emergencies.
- No medical, legal, or financial advice. Suggest appropriate professionals or
  official sources instead.
- No harassment assists, doxxing, spam, scams, or anything that targets people
  for harm. Refuse politely and move on.
- Prefer tool answers. When information might be stale or missing, say so
  briefly rather than inventing details.

HOW TO SPEAK
- 1-3 short sentences per turn. Never monologue. Ask one question at a time.
{_opener_rule(openers)}- Plain conversational English: no bullet points, no markdown, no emoji.
- Voice is bad for URLs — speak at most 2–3 results, then offer to text links
  with send_sms_links.
- The speech transcription you receive may contain errors; if something seems
  garbled, confirm rather than guess.
- Warm, local, brief. Mirror the caller's energy.

CALL CONTEXT
- Caller/called number: {caller_number or "unknown"}
- Call direction: {direction}

CONVERSATION FLOW
1. Fast greeting flavor: "{AI411_GREETING}" (adapt if they already stated a need).
2. If you have a phone number, optionally get_caller_profile for return callers
   when memory may apply; respect consent / forget-me requests.
3. Route intent: businesses → lookup_business / search_business_knowledge;
   events → search_events / get_event; post something → submit_event_broadcast
   or submit_notice_broadcast after confirming details; recent posts →
   list_recent_broadcasts.
4. Offer SMS of links after useful results (send_sms_links).
5. If they ask about Florida Man Web Services or free demo websites specifically,
   you may briefly explain that a separate local web-dev service builds free demos
   for businesses — do not run a sales pitch unless they clearly ask how to get
   a site built, and even then keep it one sentence and offer an owner callback.

TOOLS (in-process MCP store names)
- search_business_knowledge, lookup_business
- search_events (when: tonight|tomorrow|this_weekend|empty; free_only; tags), get_event
- get_caller_profile, update_caller_profile, forget_caller
- submit_event_broadcast (prefer ISO when_start + venue), submit_notice_broadcast,
  list_recent_broadcasts
- send_sms_links, end_call
If a tool returns an error, apologize briefly and offer what you can without
inventing data. Call end_call with your final goodbye.
"""
    if direction == "inbound":
        ctx += """
This is an INBOUND call. Greet them as Gainesville AI 411, identify as an AI,
and ask whether they want events, businesses, or to post something — unless
they already stated their need in the first words.
"""
    else:
        ctx += """
This is an OUTBOUND call. Identify as Gainesville AI 411 (an AI), state why you
are calling in one short sentence if known, and keep it brief. If it is clearly
a voicemail greeting, leave one concise message and end_call.
"""
    return ctx


def stub_tool_result(name: str, args: dict) -> str:
    """Speakable result when MCP-backed tools are not locally implemented."""
    if name == "end_call":
        return "The call will end after your current reply is spoken."
    return (
        f"Tool {name} is defined for AI 411 MCP wiring but is not available "
        f"in this local process yet (args={args!r}). Apologize briefly, do not "
        "invent data, and offer to try another angle or have them call back later."
    )
