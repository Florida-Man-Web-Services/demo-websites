"""Gainesville AI 411 voice mode — prompt + tool schemas (issue #51).

When AGENT_MODE=ai411 the voice agent is a local directory/events operator,
not the Florida Man Web Services sales pitch. Tool *names* match the MCP
surface; agent._run_tool dispatches in-process to mcp-server stores via
mcp_bridge (knowledge / events / callers / broadcasts / lookup).
"""

from __future__ import annotations

# Instant first-audio openers (same prewarm pattern as sales OPENERS).
OPENERS = [
    "A411 here.",
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

# Default inbound answer — keep short; do not expand into a menu monologue.
AI411_GREETING = "A411 here."

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
            "Search cached local events after you know the caller's interest or "
            "category. Prefer summarize_event_categories first when the list may "
            "be long. Pass category= from that summary to drill into one bucket."
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
                "category": {
                    "type": "string",
                    "description": (
                        "Primary category from summarize_event_categories "
                        "(music, food, arts, sports, outdoors, nightlife, family, …)."
                    ),
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
        "name": "summarize_event_categories",
        "description": (
            "Get event category counts for a time window (tonight / tomorrow / "
            "this_weekend). Use this for long lists: speak total + how many in "
            "each category, then let the caller pick a category before listing "
            "individual events."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "when": {
                    "type": "string",
                    "description": "tonight, tomorrow, this_weekend, or empty.",
                },
                "query": {
                    "type": "string",
                    "description": "Optional topic filter before categorizing.",
                },
                "free_only": {
                    "type": "boolean",
                    "description": "If true, only free events.",
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
            "interests, areas, consent flags, last topics. When the caller asks "
            "you to remember something, include consent.memory_ok=true (or "
            "consent=true) in the same patch as the preference — otherwise "
            "memory stays off and the next call will not see it."
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
                        "Fields to merge. Prefer "
                        '{"consent": {"memory_ok": true}, '
                        '"preferences": {"interests": ["…"]}, '
                        '"last_topics": ["…"]}. Also accepts consent: true.'
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
        "name": "get_question_of_the_day",
        "description": (
            "Load today's people-oriented Question of the Day. Ask it "
            "conversationally (about who they like to be around / how they hang). "
            "After they answer, call answer_question_of_the_day, then invite "
            "suggest_question_of_the_day."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "day": {
                    "type": "string",
                    "description": "Optional YYYY-MM-DD (default today ET).",
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "answer_question_of_the_day",
        "description": (
            "Save the caller's QOTD answer. Builds their long-horizon people "
            "profile (tags/interests) used to match events with like-minded vibes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {
                    "type": "string",
                    "description": "Caller phone; omit to use caller's number.",
                },
                "answer": {
                    "type": "string",
                    "description": "What the caller said in response to the QOTD.",
                },
                "question_id": {
                    "type": "string",
                    "description": "Id from get_question_of_the_day when known.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional interest tags you inferred (music, food…).",
                },
            },
            "required": ["answer"],
            "additionalProperties": False,
        },
    },
    {
        "name": "suggest_question_of_the_day",
        "description": (
            "Save a caller-suggested future Question of the Day. Prefer "
            "people-oriented questions. Call after they answer today's QOTD "
            "or when they volunteer an idea."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {
                    "type": "string",
                    "description": "Caller phone; omit to use caller's number.",
                },
                "suggestion": {
                    "type": "string",
                    "description": "The question idea they proposed.",
                },
            },
            "required": ["suggestion"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_caller_people_profile",
        "description": (
            "Summarize this caller's QOTD answers and people-interest tags "
            "accumulated over time."
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
        "name": "match_events_for_profile",
        "description": (
            "Find local events that fit the caller's people profile (QOTD answers "
            "+ interests) — use when they want hangouts with like-minded people "
            "or after building profile via QOTD."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {
                    "type": "string",
                    "description": "Caller phone; omit to use caller's number.",
                },
                "when": {
                    "type": "string",
                    "description": "tonight, tomorrow, this_weekend, or empty.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max events (default 5).",
                },
                "free_only": {
                    "type": "boolean",
                    "description": "If true, only free events.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "express_event_interest",
        "description": (
            "Record that this caller is interested in attending a specific event "
            "(by event_id from search_events / get_event / match_events_for_profile). "
            "Requires memory_ok. FOMO tribe alerts also need consent.fomo_ok "
            "(default OFF) — offer opt-in once after they pick an event. Never "
            "share peer names or phone numbers."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {
                    "type": "string",
                    "description": "Caller phone; omit to use caller's number.",
                },
                "event_id": {
                    "type": "string",
                    "description": "Event id from search_events / get_event.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional interest tags (music, food, …).",
                },
            },
            "required": ["event_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_event_interest_matches",
        "description": (
            "List privacy-safe FOMO matches: when other fomo_ok callers are "
            "interested in the same events as this caller. Speak only generic "
            "tribe lines (\"someone else into music is interested in …\") — never "
            "names or numbers. Requires memory_ok + fomo_ok."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {
                    "type": "string",
                    "description": "Caller phone; omit to use caller's number.",
                },
                "event_id": {
                    "type": "string",
                    "description": "Optional: filter to one event id.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max matches (default 10).",
                },
            },
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
    caller_profile: dict | None = None,
) -> str:
    """AI 411 operator prompt — no Florida Man $999 sales pitch."""
    memory_block = _memory_context(caller_profile)
    ctx = f"""You are Gainesville AI 411 — a helpful local phone operator for the
Gainesville, Florida community. Callers dial you for events, local businesses,
community notices, and light personalization by phone. You are an AI on a live
phone call; everything you write will be spoken aloud.

IDENTITY AND SAFETY (non-negotiable)
- First spoken line on answer (default mode): exactly "{AI411_GREETING}" — nothing
  longer. Do not add a menu, tagline, or "how can I help" on that first turn unless
  the caller already stated a need in the same beat (then skip the bare greeting
  and help immediately). You are A411 / Gainesville AI 411 (an AI); never pretend
  to be human. If asked what you are, say you are an AI briefly.
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
{memory_block}
MEMORY (phone-keyed caller profile)
- If MEMORY SNAPSHOT above shows interests or a name, use them naturally on this
  call (e.g. prioritize farmers markets if that is stored). Do not claim you
  "cannot remember" when the snapshot has data.
- When the caller asks you to remember something ("remember that I like X",
  "next time…", "my name is…"): call update_caller_profile with BOTH
  consent.memory_ok=true AND the preference/name in the same patch. Example:
  patch={{"consent": {{"memory_ok": true}}, "preferences": {{"interests": ["farmers markets"]}}, "last_topics": ["events"]}}
  Bare consent:true is also accepted. Do not only ask for consent without saving.
- If they say yes to enabling memory, set consent.memory_ok=true immediately.
- FOMO tribe alerts are separate and DEFAULT OFF. Only set consent.fomo_ok=true
  (or preferences.fomo_calls=true) after an explicit yes to FOMO / "run with your
  tribe" alerts. Prefer also preferences.sms_ok=true so we text instead of call.
  Bare memory consent does NOT enable FOMO.
- forget_caller when they ask to be forgotten / wipe memory.
- Still call get_caller_profile if you need a fresh read mid-call.

CONVERSATION FLOW
1. Answer with exactly: "{AI411_GREETING}" Then wait. Do not expand the greeting.
2. Prefer the MEMORY SNAPSHOT; optionally get_caller_profile if snapshot missing.
3. Route intent:
   - question of the day / get to know me / bored → QUESTION OF THE DAY flow
   - events with people like me / like-minded / who should I hang with →
     match_events_for_profile (after profile has signal) or QOTD first
   - businesses → lookup_business / search_business_knowledge
   - events → follow EVENT DISCOVERY below (not a raw dump)
   - post something → submit_event_broadcast / submit_notice_broadcast after confirm
   - recent posts → list_recent_broadcasts
4. Offer SMS of links after useful results (send_sms_links).
5. If they ask about Florida Man Web Services or free demo websites specifically,
   you may briefly explain that a separate local web-dev service builds free demos
   for businesses — do not run a sales pitch unless they clearly ask how to get
   a site built, and even then keep it one sentence and offer an owner callback.

QUESTION OF THE DAY (people profile over time)
Purpose: learn how this caller likes to be around *people*, build a durable
profile, then match events where they might find like-minded folks.
1. Call get_question_of_the_day. Ask the question in one short spoken turn.
   These questions are about people (crowds, hangouts, who they click with) —
   not trivia.
2. Listen. Call answer_question_of_the_day with their answer (and tags if clear).
   That enables memory and stores interests.
3. Invite a suggestion: ask if they have a good people-question for other
   callers tomorrow. If they offer one, call suggest_question_of_the_day.
4. Optionally offer match_events_for_profile (with when= if they gave a window)
   so they can find hangouts that fit their vibe. Speak 2–3 events max.
5. On return callers, get_caller_people_profile or MEMORY SNAPSHOT first; you
   may skip QOTD if they already answered today or want events immediately —
   but still offer QOTD once if the conversation is open-ended.

EVENT DISCOVERY (required whenever they ask what's going on / events / tonight /
this weekend / etc.)
1. Interests first (mandatory): If MEMORY SNAPSHOT or people profile already has
   interests, briefly acknowledge them and use them as the topic. Prefer
   match_events_for_profile when they want people/like-minded matches. If no
   interests yet, ask ONE short question about what they like (music, food,
   outdoors, family, free stuff, arts…) OR offer today's QOTD — and WAIT.
   Do NOT call search_events or summarize_event_categories and do NOT list
   event titles until you have an interest, a QOTD answer, or they refuse and
   say "anything" / "everything".
2. Time window: map their words to when= tonight | tomorrow | this_weekend | empty.
3. Long list → categories: Call summarize_event_categories with that when= (and
   optional query= their interest). If long_list is true or total > 3, speak only
   the total and how many events in each category — e.g. "Twelve things this
   weekend: four music, three food, two arts." Then ask which category they want.
   Do not read every title yet.
4. Drill-down: When they pick a category (or a specific interest that maps to one),
   call search_events with category= that name and the same when=. Speak at most
   2–3 event titles with one detail each; offer more or SMS links.
5. Short list (≤3 total): After interests are known, you may name the events
   directly without the category round.
6. Save new interests they state via update_caller_profile (with memory_ok).
7. FOMO / tribe interest (after they pick or clearly like a specific event):
   a. Call express_event_interest with that event_id (needs memory_ok first).
   b. Offer FOMO opt-in ONCE if needs_fomo_ok: brief explanation — if others into
      the same things are interested in the same event, you can tip them off;
      never share names or phone numbers; default off; prefer text if they allow SMS.
   c. On yes: update_caller_profile with consent.fomo_ok=true (keep memory_ok)
      and preferences.sms_ok=true if they agree to texts; then express_event_interest
      again or list_event_interest_matches.
   d. On no: leave fomo_ok false; do not nag again this call.
   e. Speak only privacy-safe lines from the tool (someone else into X is
      interested in Y). Never invent peer names or numbers.

TOOLS (in-process MCP store names)
- search_business_knowledge, lookup_business
- summarize_event_categories, search_events (when; category; tags; free_only), get_event
- get_caller_profile, update_caller_profile, forget_caller
- get_question_of_the_day, answer_question_of_the_day, suggest_question_of_the_day
- get_caller_people_profile, match_events_for_profile
- express_event_interest, list_event_interest_matches
- submit_event_broadcast (prefer ISO when_start + venue), submit_notice_broadcast,
  list_recent_broadcasts
- send_sms_links, end_call
If a tool returns an error, apologize briefly and offer what you can without
inventing data. Call end_call with your final goodbye.
"""
    if direction == "inbound":
        ctx += f"""
This is an INBOUND call in default AI 411 mode. Your very first spoken words must
be exactly: "{AI411_GREETING}" — stop there and listen. Do not list events,
businesses, or posting options until they speak. If their first audio already
states a need, skip the bare greeting and help immediately.
"""
    else:
        ctx += f"""
This is an OUTBOUND call. Open with "{AI411_GREETING}" then one short reason for
the call if known. Keep it brief. If it is clearly a voicemail greeting, leave
one concise message and end_call.
"""
    return ctx


def _memory_context(profile: dict | None) -> str:
    """Compact speakable memory block injected at call start."""
    if not profile or not profile.get("found"):
        return (
            "\nMEMORY SNAPSHOT\n"
            "- No stored profile for this number yet (or memory not enabled).\n"
        )
    if not profile.get("memory_ok"):
        return (
            "\nMEMORY SNAPSHOT\n"
            "- Profile exists but memory_ok is false — do not personalize from "
            "storage; you may ask to enable memory.\n"
        )
    prefs = profile.get("preferences") or {}
    interests = prefs.get("interests") or []
    avoid = prefs.get("avoid") or []
    areas = prefs.get("preferred_areas") or []
    name = (profile.get("preferred_name") or profile.get("display_name") or "").strip()
    topics = profile.get("last_topics") or []
    notes = profile.get("notes") or []
    note_bits = []
    for n in notes[-3:]:
        if isinstance(n, dict) and n.get("text"):
            note_bits.append(str(n["text"])[:120])
        elif isinstance(n, str) and n.strip():
            note_bits.append(n.strip()[:120])
    lines = ["\nMEMORY SNAPSHOT (use on this call — already consented)"]
    if name:
        lines.append(f"- Name: {name}")
    if interests:
        lines.append(f"- Interests: {', '.join(str(x) for x in interests)}")
    consent = profile.get("consent") or {}
    if consent.get("fomo_ok") or prefs.get("fomo_calls"):
        lines.append(
            "- FOMO tribe alerts: ON (may tip about shared event interest; "
            "never peer names/numbers)"
        )
    else:
        lines.append("- FOMO tribe alerts: OFF (default; offer opt-in once after event pick)")
    if avoid:
        lines.append(f"- Avoid: {', '.join(str(x) for x in avoid)}")
    if areas:
        lines.append(f"- Preferred areas: {', '.join(str(x) for x in areas)}")
    if topics:
        lines.append(f"- Last topics: {', '.join(str(x) for x in topics)}")
    if note_bits:
        lines.append(f"- Notes: {'; '.join(note_bits)}")
    if len(lines) == 1:
        lines.append("- Memory on, but no interests/name stored yet.")
    lines.append("")
    return "\n".join(lines) + "\n"


def stub_tool_result(name: str, args: dict) -> str:
    """Speakable result when MCP-backed tools are not locally implemented."""
    if name == "end_call":
        return "The call will end after your current reply is spoken."
    return (
        f"Tool {name} is defined for AI 411 MCP wiring but is not available "
        f"in this local process yet (args={args!r}). Apologize briefly, do not "
        "invent data, and offer to try another angle or have them call back later."
    )
