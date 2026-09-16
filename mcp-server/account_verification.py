"""Server-owned authentication primitives for account lifecycle boundaries.

This module intentionally contains records and capability checks only.  It does
not call a live identity, telephony, or verification provider.  A later task
can create these records after authenticating through a trusted transport and
pass them to lifecycle APIs without allowing model-supplied mappings to stand
in for server-owned proof.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class AuthContext:
    """Typed, action-bound server authentication context.

    ``capability_id`` identifies the server-issued capability associated with
    this action.  This record contains no secret, phone number, or provider
    client and is safe to pass between trusted server modules.
    """

    session_id: str
    account_id: str
    caller_transport_binding: str
    auth_revision: int
    action: str
    expires_at: datetime | str
    capability_id: str
    account_status: str
    owner_authenticated: bool
    proof_fresh: bool
    auth_level: str | None = None
    legacy_auth: bool = False

    def has_capability(self, action: str) -> bool:
        """Return whether this context is bound to exactly ``action``."""

        return (
            self.action == action
            and isinstance(self.capability_id, str)
            and bool(self.capability_id.strip())
        )


@dataclass(frozen=True, slots=True)
class TrustedInputEvent:
    """Typed server-validated private input event.

    Only opaque references/digests belong in this record.  ``value_digest``
    may bind private input without retaining the input itself.
    """

    event_id: str
    session_id: str
    account_id: str
    caller_transport_binding: str
    auth_revision: int
    action: str
    expires_at: datetime | str
    capability_id: str
    event_type: str
    value_digest: str | None = None

    def has_capability(self, action: str) -> bool:
        """Return whether this trusted event is bound to exactly ``action``."""

        return (
            self.action == action
            and isinstance(self.capability_id, str)
            and bool(self.capability_id.strip())
        )


def is_auth_context(value: Any) -> bool:
    """Require the exact server primitive rather than duck-typed lookalikes."""

    return type(value) is AuthContext


def is_trusted_input_event(value: Any) -> bool:
    """Require the exact trusted-input primitive rather than a mapping/object."""

    return type(value) is TrustedInputEvent


__all__ = [
    "AuthContext",
    "TrustedInputEvent",
    "is_auth_context",
    "is_trusted_input_event",
]
