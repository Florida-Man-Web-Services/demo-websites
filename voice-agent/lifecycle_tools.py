"""Safe model-facing schemas for the disabled-by-default account lifecycle.

These schemas contain request data only. Server-owned call state supplies
identity, authorization, OTP results, destination numbers, and confirmation.
"""

from __future__ import annotations

LIFECYCLE_TOOL_NAMES = frozenset(
    {
        "request_account_step_up",
        "get_account_lifecycle_status",
        "prepare_client_page",
        "prepare_trusted_phone_add",
        "prepare_trusted_phone_removal",
        "prepare_client_page_removal",
        "cancel_account_operation",
    }
)

LIFECYCLE_TOOLS = [
    {
        "name": "request_account_step_up",
        "description": "Request server-controlled owner verification for one account action. The code is never returned to the model.",
        "input_schema": {
            "type": "object",
            "properties": {"action": {"type": "string", "enum": ["client_page_create", "client_page_remove", "trusted_phone_add", "trusted_phone_remove"]}},
            "required": ["action"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_account_lifecycle_status",
        "description": "Read safe status for the current caller's pending account operation. Never returns phones, OTPs, account ids, or page bodies.",
        "input_schema": {
            "type": "object",
            "properties": {"operation_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "prepare_client_page",
        "description": "Prepare a bounded plain-text client page after server verification. Preparation is not publication and requires keypad confirmation later.",
        "input_schema": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "body": {"type": "string"}, "idempotency_key": {"type": "string"}},
            "required": ["title", "body", "idempotency_key"],
            "additionalProperties": False,
        },
    },
    {
        "name": "prepare_trusted_phone_add",
        "description": "Prepare a trusted-phone addition. Destination capture and destination OTP verification happen only through private server telephony events.",
        "input_schema": {
            "type": "object",
            "properties": {"idempotency_key": {"type": "string"}},
            "required": ["idempotency_key"],
            "additionalProperties": False,
        },
    },
    {
        "name": "prepare_trusted_phone_removal",
        "description": "Prepare removal of a trusted phone using an opaque masked phone reference returned by the server.",
        "input_schema": {
            "type": "object",
            "properties": {"phone_ref": {"type": "string"}, "idempotency_key": {"type": "string"}},
            "required": ["phone_ref", "idempotency_key"],
            "additionalProperties": False,
        },
    },
    {
        "name": "prepare_client_page_removal",
        "description": "Prepare removal of the current account's client page. The page is not removed until server-controlled keypad confirmation.",
        "input_schema": {
            "type": "object",
            "properties": {"page_id": {"type": "string"}, "idempotency_key": {"type": "string"}},
            "required": ["page_id", "idempotency_key"],
            "additionalProperties": False,
        },
    },
    {
        "name": "cancel_account_operation",
        "description": "Cancel the current caller's pending account operation. This is not a publication or phone mutation.",
        "input_schema": {
            "type": "object",
            "properties": {"operation_id": {"type": "string"}},
            "required": ["operation_id"],
            "additionalProperties": False,
        },
    },
]
