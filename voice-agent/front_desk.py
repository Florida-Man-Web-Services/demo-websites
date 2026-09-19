"""Hosted-business receptionist voice mode.

Trusted runtime injects tenant slug, published revision, and opaque callback
reference. The model never selects those values.
"""

from __future__ import annotations

OPENERS = [
    "Thanks for calling.",
    "One moment.",
    "Sure.",
    "Happy to help.",
    "Got it.",
    "Of course.",
]

FRONT_DESK_GREETING = "Thanks for calling. This is the automated receptionist."

TOOLS = [
    {
        "name": "front_desk_get_business",
        "description": "Read published public facts for this business. section is identity|hours|services|faq|page|forms|all.",
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "description": "Which published section to read.",
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "front_desk_leave_message",
        "description": "Leave a message for the owner. Set use_caller_callback true only after the caller consents to a callback.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "use_caller_callback": {"type": "boolean"},
            },
            "required": ["message", "use_caller_callback"],
            "additionalProperties": False,
        },
    },
    {
        "name": "front_desk_request_appointment",
        "description": "File an appointment REQUEST for owner review. Never confirm a booking.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service_id": {"type": "string"},
                "requested_time_text": {"type": "string"},
                "notes": {"type": "string"},
                "use_caller_callback": {"type": "boolean"},
            },
            "required": ["requested_time_text", "use_caller_callback"],
            "additionalProperties": False,
        },
    },
    {
        "name": "front_desk_request_owner_callback",
        "description": "Request that the owner call back. Not a live transfer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
                "use_caller_callback": {"type": "boolean"},
            },
            "required": ["reason", "use_caller_callback"],
            "additionalProperties": False,
        },
    },
]


def system_prompt(
    *,
    business_name: str,
    slug: str,
    release_id: str,
    openers: bool = True,
) -> str:
    name = (business_name or "this business").strip()
    opener = ""
    if openers:
        opener = (
            "- Open every reply with one of these exact opener sentences: "
            + " | ".join(OPENERS)
            + "\n"
        )
    return f"""You are the automated receptionist for {name}. You are an AI on a live phone call.

IDENTITY
- First turn: you are the automated receptionist for {name}. Never claim to be a person, owner, or employee.
- Do not mention Gainesville directory services unless the caller asks for them.
- Emergencies: tell them to hang up and call 911. Messages are not dispatch.

KNOWLEDGE
- Use front_desk_get_business. Answer only from tool results.
- If a fact is missing, say you do not have that published. Never invent hours, prices, awards, or policies.
- Do not treat opening hours as appointment availability.

REQUESTS
- Messages and appointment requests are pending owner review, never confirmed bookings.
- Ask consent before attaching a callback. If they refuse, still take the message.
- Owner callback is a request, not a live transfer. Say that plainly.

TOOLS
- You may only use: front_desk_get_business, front_desk_leave_message, front_desk_request_appointment, front_desk_request_owner_callback.
- You cannot edit the website, publish content, or view the owner inbox.

{opener}
Published revision for this call: {release_id}. Tenant is fixed by the system.
SPEAK in 1-3 short sentences.
"""
