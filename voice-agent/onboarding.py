"""Onboarding voice mode — requirements interview for new website customers.

Triggered when customers.register_callback queues a phone, or status is
onboarding/callback_queued. Collects open-ended business + website needs,
saves via customers.save_requirements, and hands off to the builder + sales.
"""

from __future__ import annotations

OPENERS = [
    "Thanks for calling.",
    "Sure thing.",
    "Got it.",
    "Absolutely.",
    "Of course.",
    "That helps.",
    "Good question.",
    "One moment.",
    "Perfect.",
    "Understood.",
]

ONBOARDING_GREETING = (
    "Thanks for requesting a callback — I'm an AI helping design your free demo website. "
    "What kind of business are we building for?"
)

TOOLS = [
    {
        "name": "get_customer_profile",
        "description": (
            "Load what we already know about this caller (signup form, prior "
            "answers). Call once near the start."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {
                    "type": "string",
                    "description": "Caller phone; omit to use the line's number.",
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "save_onboarding_answer",
        "description": (
            "Save one structured answer mid-interview (business_name, category, "
            "goals, pages, branding, content_sources, must_haves, timeline, email, "
            "notes). Call often so progress is not lost if they hang up."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "field": {
                    "type": "string",
                    "description": "Field key, e.g. business_name, goals, pages.",
                },
                "value": {
                    "description": "String, list, or object value for that field.",
                },
                "phone": {"type": "string"},
            },
            "required": ["field", "value"],
            "additionalProperties": False,
        },
    },
    {
        "name": "finalize_requirements",
        "description": (
            "After reading back the plan and getting confirmation, save the full "
            "requirements package and mark the customer requirements_ready for "
            "the website builder. Include a short spoken summary."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "1-3 sentence summary of the website brief.",
                },
                "requirements": {
                    "description": (
                        "Object or JSON string: business_name, category, audience, "
                        "goals, pages[], features[], branding, tone, content_notes, "
                        "must_haves[], nice_to_haves[], timeline, email, phone."
                    ),
                },
                "business_name": {"type": "string"},
                "category": {"type": "string"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "confirmation_spoken": {
                    "type": "boolean",
                    "description": "True only after you read back and they agreed.",
                },
            },
            "required": ["summary", "requirements"],
            "additionalProperties": False,
        },
    },
    {
        "name": "queue_website_build",
        "description": (
            "After finalize_requirements, write a builder brief for the coding "
            "agent (GitHub-backed site build). Safe to call once per interview."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"phone": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "send_sms_links",
        "description": "Text a confirmation or FAQ link to the caller.",
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {"type": "string"},
                "links": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "note": {"type": "string"},
            },
            "required": ["links"],
            "additionalProperties": False,
        },
    },
    {
        "name": "log_call_outcome",
        "description": (
            "Log how the onboarding call went. Use once before hangup. "
            "Prefer outcome other/callback_requested/voicemail/do_not_call; "
            "notes should mention if requirements were finalized."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "outcome": {
                    "type": "string",
                    "enum": [
                        "interested",
                        "callback_requested",
                        "voicemail",
                        "do_not_call",
                        "wrong_number",
                        "other",
                    ],
                },
                "email": {"type": "string"},
                "callback_time": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["outcome", "notes"],
            "additionalProperties": False,
        },
    },
    {
        "name": "end_call",
        "description": "Hang up after your goodbye is spoken.",
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
    return (
        f"- Open every reply with one of these exact opener sentences "
        f'(vary them): {" | ".join(OPENERS)}\n'
    )


def system_prompt(
    *,
    direction: str,
    caller_number: str,
    openers: bool = True,
    customer: dict | None = None,
) -> str:
    cust = customer or {}
    known = ""
    if cust:
        known = f"""
WHAT WE ALREADY KNOW (from signup / prior turns)
- Business: {cust.get("business_name") or "unknown"}
- Contact: {cust.get("contact_name") or "unknown"}
- Email: {cust.get("email") or "unknown"}
- Status: {cust.get("status") or "unknown"}
- Prior summary: {cust.get("requirements_summary") or "(none yet)"}
"""
    ctx = f"""You are Florida Man Web Services' **onboarding interview AI** on a live
phone call. Your only job is to help a local business owner flesh out
requirements for a free demo website — open-ended questions, not a hard sell.

You are NOT the sales closer and NOT the community directory operator. Do not
quote monthly prices unless they ask; if they do, say looking at the demo is
free and going live is discussed when the demo is ready.

IDENTITY AND SAFETY
- First turn: identify as an AI helping design their free demo site.
- Emergencies → 911. No medical/legal/financial advice.
- Warm, curious, concise. 1-3 short sentences. One question at a time.
{_opener_rule(openers)}- Confirm unclear speech. No markdown or emoji.

CALL CONTEXT
- Caller number: {caller_number or "unknown"}
- Direction: {direction}
{known}
INTERVIEW FLOW
1. Greet: "{ONBOARDING_GREETING}" (adapt if you already know the business name).
2. get_customer_profile once.
3. Explore open-ended (use save_onboarding_answer after each solid answer):
   - What the business is and who they serve
   - What a great website would do for them (goals)
   - Must-have pages/sections (home, services, menu, gallery, contact, …)
   - Branding/tone (colors, feel) if they care
   - Content they already have (photos, logo, Google listing)
   - Must-haves vs nice-to-haves; any hard deadline
   - Best email for sending the demo later
4. Read the plan back in plain language. Get explicit confirmation.
5. finalize_requirements with confirmation_spoken=true, a summary, and the
   full requirements object.
6. queue_website_build so the coding agent can start.
7. Tell them we'll call or text when the free demo is ready; log_call_outcome;
   end_call.

TOOLS
- get_customer_profile, save_onboarding_answer, finalize_requirements,
  queue_website_build, send_sms_links, log_call_outcome, end_call
Never invent NAP facts; capture what they say. If they only want the local
directory, say this line is for website onboarding and they can call back for
community info.
"""
    if direction == "outbound":
        ctx += """
This is an OUTBOUND callback they requested on the AI 411 site. Open with AI
disclosure and thank them for the request, then start the interview. Voicemail:
leave a short callback message and log voicemail.
"""
    elif direction == "sms":
        ctx += """
This is SMS onboarding. Keep replies short; still run the same interview tools.
"""
    else:
        ctx += """
This is an INBOUND call from someone in the onboarding queue (or continuing
an interview). Resume from get_customer_profile.
"""
    return ctx


def stub_tool_result(name: str, args: dict) -> str:
    if name == "end_call":
        return "The call will end after your current reply is spoken."
    return (
        f"Tool {name} is not available right now (args={args!r}). "
        "Apologize briefly and continue the interview from memory, or offer a callback."
    )
