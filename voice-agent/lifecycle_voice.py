"""Private voice transport boundary for the account lifecycle.

This module is deliberately not a model tool adapter.  It is called by the
telephony/DTMF path with server-validated call state.  Model text and model
arguments cannot create AuthContext, TrustedInputEvent, OTP input refs, or
confirmation tokens.
"""

from __future__ import annotations

import secrets
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_MCP_DIR = Path(__file__).resolve().parent.parent / "mcp-server"
if str(_MCP_DIR) not in sys.path:
    sys.path.insert(0, str(_MCP_DIR))

import account_verification as verification


LIFECYCLE_ACTIONS = frozenset(
    {
        "client_page_create",
        "client_page_remove",
        "trusted_phone_add",
        "trusted_phone_remove",
    }
)


def _expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=600)


def _state_auth(state: Any, action: str | None = None) -> verification.AuthContext | dict[str, Any]:
    auth = getattr(state, "lifecycle_auth", None)
    binding = getattr(state, "lifecycle_transport_binding", None)
    if (
        verification.is_auth_context(auth)
        and verification.validate_transport_session_binding(binding)
        and isinstance(binding, verification.TransportSessionBinding)
        and auth.session_id == binding.session_id
        and auth.caller_transport_binding == binding.transport_id
        and auth.account_id == binding.account_id
        and (action is None or auth.action == action)
    ):
        return auth
    return create_auth_context(state, action=action or getattr(state, "lifecycle_action", ""))


def create_auth_context(state: Any, *, action: str) -> verification.AuthContext | dict[str, Any]:
    """Create an unprivileged context from server call state, never caller text."""

    if not verification.lifecycle_enabled():
        return {"ok": False, "state": "denied", "code": "feature_disabled"}
    if action not in LIFECYCLE_ACTIONS:
        return {"ok": False, "state": "denied", "code": "invalid_action"}
    binding = getattr(state, "lifecycle_transport_binding", None)
    if not (
        verification.validate_transport_session_binding(binding)
        and isinstance(binding, verification.TransportSessionBinding)
    ):
        return {"ok": False, "state": "denied", "code": "transport_unavailable"}
    auth = verification._issue_auth_context(
        session_id=binding.session_id,
        account_id=binding.account_id,
        caller_transport_binding=binding.transport_id,
        auth_revision=binding.auth_revision,
        action=action,
        expires_at=_expiry(),
        capability_id="cap_" + secrets.token_urlsafe(20),
        account_status=binding.account_status,
        owner_authenticated=False,
        proof_fresh=False,
        auth_level="server_pending",
    )
    state.lifecycle_auth = auth
    state.lifecycle_action = action
    return auth


build_auth_context = create_auth_context


def capture_private_secret_input(
    state: Any, secret_input: str, *, purpose: str, challenge_id: str | None = None
) -> str | dict[str, Any]:
    """Capture a telephony-private value; the model receives only the ref."""

    auth = _state_auth(state)
    if not verification.is_auth_context(auth):
        return auth
    try:
        return verification.capture_secret_input(
            secret_input, auth=auth, purpose=purpose, challenge_id=challenge_id
        )
    except ValueError:
        return {"ok": False, "state": "denied", "code": "private_input_unavailable"}


capture_secret_input = capture_private_secret_input


def request_owner_step_up(state: Any, *, action: str) -> dict[str, Any]:
    auth = _state_auth(state, action)
    if not verification.is_auth_context(auth):
        return auth
    state.lifecycle_auth = auth
    return verification.request_owner_verification(auth=auth, purpose="owner_step_up")


def complete_owner_step_up(state: Any, *, challenge_id: str, secret_input_ref: str) -> dict[str, Any]:
    auth = _state_auth(state)
    if not verification.is_auth_context(auth):
        return auth
    out = verification.complete_step_up(
        challenge_id=challenge_id, secret_input_ref=secret_input_ref, ctx=auth
    )
    if out.get("state") == "verified_success" and verification.is_auth_context(out.get("auth")):
        state.lifecycle_auth = out["auth"]
        state.lifecycle_step_up_ok = True
    return out


def capture_account_phone(state: Any, *, secret_input_ref: str) -> dict[str, Any]:
    auth = _state_auth(state, "trusted_phone_add")
    if not verification.is_auth_context(auth):
        return auth
    return verification.capture_account_phone(auth=auth, secret_input_ref=secret_input_ref)


def make_send_consent_event(
    state: Any, *, operation_id: str
) -> verification.TrustedInputEvent | dict[str, Any]:
    """Create an operation-bound event only from a separate DTMF boundary."""
    auth = _state_auth(state, "trusted_phone_add")
    if not verification.is_auth_context(auth) or not auth.owner_authenticated:
        return {"ok": False, "state": "denied", "code": "verification_required"}
    try:
        return verification._issue_trusted_input_event(
            auth=auth, event_type="send_consent", operation_id=operation_id,
        )
    except ValueError:
        return {"ok": False, "state": "denied", "code": "invalid_consent_event"}


def request_destination_verification(
    state: Any, *, operation_id: str, consent_event: verification.TrustedInputEvent
) -> dict[str, Any]:
    auth = _state_auth(state, "trusted_phone_add")
    if not verification.is_auth_context(auth):
        return auth
    return verification.request_destination_verification(
        auth=auth, operation_id=operation_id, send_consent_event=consent_event
    )


def verify_destination(
    state: Any, *, operation_id: str, challenge_id: str, secret_input_ref: str
) -> dict[str, Any]:
    auth = _state_auth(state, "trusted_phone_add")
    if not verification.is_auth_context(auth):
        return auth
    return verification.verify_destination_challenge(
        auth=auth,
        operation_id=operation_id,
        challenge_id=challenge_id,
        secret_input_ref=secret_input_ref,
    )


def get_readback(state: Any, *, operation_id: str) -> dict[str, Any]:
    auth = _state_auth(state)
    if not verification.is_auth_context(auth):
        return auth
    return verification.get_confirmation_readback(auth=auth, operation_id=operation_id)


def make_keypad_event(
    state: Any, *, digit: str, operation_id: str | None = None
) -> verification.TrustedInputEvent | dict[str, Any]:
    """Convert validated DTMF into an exact operation-bound event."""
    auth = _state_auth(state)
    if not verification.is_auth_context(auth) or not auth.owner_authenticated:
        return {"ok": False, "state": "denied", "code": "verification_required"}
    if digit == "2":
        if not isinstance(operation_id, str) or not operation_id:
            return {"ok": False, "state": "denied", "code": "confirmation_cancelled"}
        cancelled = verification.cancel_lifecycle_operation(
            auth=auth, operation_id=operation_id
        )
        if cancelled.get("state") == "verified_noop" and cancelled.get("code") == "cancelled":
            return {
                "ok": False,
                "state": "denied",
                "code": "confirmation_cancelled",
                "operation_id": operation_id,
            }
        return cancelled
    if digit != "1":
        return {"ok": False, "state": "denied", "code": "invalid_keypad"}
    try:
        return verification._issue_trusted_input_event(
            auth=auth, event_type="keypad_confirm", operation_id=operation_id,
        )
    except ValueError:
        return {"ok": False, "state": "denied", "code": "invalid_keypad"}


def capture_keypad_confirmation(
    state: Any, *, operation_id: str, readback_digest: str, event: verification.TrustedInputEvent
) -> str | dict[str, Any]:
    auth = _state_auth(state)
    if not verification.is_auth_context(auth):
        return auth
    if not verification.is_trusted_input_event(event):
        return {"ok": False, "state": "denied", "code": "invalid_confirmation_event"}
    return verification.capture_lifecycle_confirmation(
        auth=auth, operation_id=operation_id, readback_digest=readback_digest, event=event
    )


# Explicitly keep lifecycle operations out of model tool dispatch until Task 4.
MODEL_LIFECYCLE_NAMES = frozenset(
    {
        "request_account_step_up",
        "capture_account_phone",
        "request_destination_verification",
        "verify_destination_challenge",
        "get_confirmation_readback",
        "capture_lifecycle_confirmation",
    }
)


__all__ = [
    "LIFECYCLE_ACTIONS",
    "MODEL_LIFECYCLE_NAMES",
    "create_auth_context",
    "build_auth_context",
    "capture_private_secret_input",
    "capture_secret_input",
    "request_owner_step_up",
    "complete_owner_step_up",
    "capture_account_phone",
    "make_send_consent_event",
    "request_destination_verification",
    "verify_destination",
    "get_readback",
    "make_keypad_event",
    "capture_keypad_confirmation",
]
