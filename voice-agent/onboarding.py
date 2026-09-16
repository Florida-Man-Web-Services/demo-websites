"""Onboarding voice mode — requirements interview for new website customers.

Triggered when customers.register_callback queues a phone, or status is
onboarding/callback_queued. Collects open-ended business + website needs,
saves via customers.save_requirements, and hands off to the builder + sales.
"""

from __future__ import annotations

# The first pass deliberately stays small.  These names are the persisted
# requirements keys used by the interview and by the builder brief.  Optional
# details can improve a site, but must not hold up a useful first draft.
MVP_REQUIRED_FIELDS = (
    "business_name",
    "audience",
    "goal",
    "must_haves",
    "follow_up",
)
OPTIONAL_FIELDS = (
    "category",
    "pages",
    "features",
    "branding",
    "tone",
    "content_sources",
    "timeline",
    "email",
    "notes",
)

_FIELD_ALIASES = {
    "business_name": ("business_name", "business"),
    "goal": ("goal", "goals"),
    "follow_up": ("follow_up", "follow-up", "next_step"),
}


def _as_requirements(value: object) -> dict:
    """Return a requirements mapping without raising on model-shaped input."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        import json

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def requirements_for_customer(customer: dict | None) -> dict:
    """Merge incremental requirements with signup fields for completion checks."""
    customer = customer or {}
    requirements = _as_requirements(customer.get("requirements"))
    # Signup data is already a valid answer, even when the caller has not
    # repeated it during the callback.
    if customer.get("business_name") and not requirements.get("business_name"):
        requirements["business_name"] = customer["business_name"]
    if customer.get("category") and not requirements.get("category"):
        requirements["category"] = customer["category"]
    if customer.get("email") and not requirements.get("email"):
        requirements["email"] = customer["email"]
    return requirements


def missing_mvp_fields(requirements: dict | str | None) -> tuple[str, ...]:
    """List required first-pass fields that still need a meaningful answer."""
    values = _as_requirements(requirements)
    missing = []
    for field in MVP_REQUIRED_FIELDS:
        aliases = _FIELD_ALIASES.get(field, (field,))
        if not any(_has_answer(values.get(alias)) for alias in aliases):
            missing.append(field)
    return tuple(missing)


def _has_answer(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def mvp_brief_complete(requirements: dict | str | None) -> bool:
    """Return whether the minimum useful onboarding brief is complete."""
    return not missing_mvp_fields(requirements)


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
            "Save one structured answer immediately after a solid answer. First "
            "collect the MVP fields business_name, audience, goal, must_haves, and "
            "follow_up; optional fields are category, pages, features, branding, "
            "tone, content_sources, timeline, email, and notes. Call often so "
            "progress is not lost if they hang up."
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
                        "Object or JSON string. Required MVP keys: business_name, "
                        "audience, goal, must_haves, follow_up. Optional keys: "
                        "category, pages[], features[], branding, tone, "
                        "content_sources, timeline, email, notes."
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
            "required": ["summary", "requirements", "confirmation_spoken"],
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
        stored_requirements = requirements_for_customer(cust)
        missing = missing_mvp_fields(stored_requirements)
        known = f"""
WHAT WE ALREADY KNOW (from signup / prior turns)
- Business: {cust.get("business_name") or "unknown"}
- Contact: {cust.get("contact_name") or "unknown"}
- Email: {cust.get("email") or "unknown"}
- Status: {cust.get("status") or "unknown"}
- Prior summary: {cust.get("requirements_summary") or "(none yet)"}
- MVP fields still needed: {", ".join(missing) if missing else "none"}
Do not re-ask an MVP field that is already meaningfully answered. Resume with
the first missing MVP field, then offer optional details.
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
3. Complete the MVP FIRST, one open-ended question at a time. After each solid
   answer call save_onboarding_answer immediately. Ask, in this order:
   a. BUSINESS — what the business is called and does (save business_name; save
      category too only if they volunteer it).
   b. AUDIENCE — who they most want the website to help.
   c. GOAL — what they want the website to help them accomplish (save goal,
      not a list of speculative metrics).
   d. MUST-HAVES — pages, information, or actions the first version must include
      (save must_haves as a list or faithful short description).
   e. FOLLOW-UP — the preferred next step after this call, such as when/how to
      contact them about the demo (save follow_up).
   Do not ask optional discovery questions until all five MVP fields have
   meaningful answers. If the caller volunteers optional details, acknowledge
   and save them without opening a new line of questioning.
4. Only after the MVP is complete, progressively disclose optional details as
   time and caller interest allow: category, pages/features beyond the
   must-haves, branding/tone, content_sources, timeline, email, and notes.
   These are helpful, never gates for a first brief; do not pressure the caller.
5. Read the plan back in plain language, including the five MVP fields. Ask for
   explicit confirmation and correct anything they change.
6. Only after they agree, call finalize_requirements with confirmation_spoken=true,
   a short summary, and the full requirements object. Wait for a successful
   result before calling queue_website_build; never queue a partial brief.
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
