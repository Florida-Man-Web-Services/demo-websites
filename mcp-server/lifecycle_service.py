"""Short-lived capability bridge for authenticated lifecycle service calls.

The MCP transport authenticates the caller; this module adds an independent,
audience-bound capability so a service call cannot reuse a capability in the
wrong operation or after expiry. Tokens contain authorization metadata only—no
OTP, phone number, confirmation token, or other secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Iterable

import account_verification as verification

_ENV_KEY = "ACCOUNT_LIFECYCLE_SERVICE_KEY"
_TOKEN_VERSION = 1
_DEFAULT_TTL = 60


def _key() -> bytes:
    value = (os.getenv(_ENV_KEY) or "").encode("utf-8")
    return value


def _enc(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _dec(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign(encoded_payload: str) -> str:
    return _enc(hmac.new(_key(), encoded_payload.encode("ascii"), hashlib.sha256).digest())


def mint_service_capability(ctx: verification.AuthContext, *, audience: str, ttl: int = _DEFAULT_TTL) -> str:
    """Mint only from a currently valid, owner-verified context."""
    if not _key() or not verification.is_auth_context(ctx):
        raise ValueError("service capability unavailable")
    if not ctx.owner_authenticated or not ctx.proof_fresh:
        raise ValueError("fresh owner proof required")
    if not isinstance(audience, str) or not audience or len(audience) > 120:
        raise ValueError("invalid audience")
    now = int(time.time())
    payload = {
        "v": _TOKEN_VERSION,
        "aud": audience,
        "iat": now,
        "exp": now + max(1, min(int(ttl), 120)),
        "nonce": secrets.token_urlsafe(12),
        "session_id": ctx.session_id,
        "account_id": ctx.account_id,
        "caller_transport_binding": ctx.caller_transport_binding,
        "auth_revision": ctx.auth_revision,
        "action": ctx.action,
        "capability_id": ctx.capability_id,
        "account_status": ctx.account_status,
        "owner_authenticated": True,
        "proof_fresh": True,
        "auth_level": ctx.auth_level,
    }
    encoded = _enc(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return f"{encoded}.{_sign(encoded)}"


def _payload(token: str, *, audience: str, actions: Iterable[str]) -> dict[str, Any] | None:
    if not _key() or not isinstance(token, str) or token.count(".") != 1:
        return None
    encoded, signature = token.split(".", 1)
    expected = _sign(encoded)
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_dec(encoded))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    now = int(time.time())
    if (
        payload.get("v") != _TOKEN_VERSION
        or payload.get("aud") != audience
        or not isinstance(payload.get("exp"), int)
        or payload["exp"] < now
        or not isinstance(payload.get("iat"), int)
        or payload["iat"] > now + 10
        or payload.get("action") not in set(actions)
        or payload.get("owner_authenticated") is not True
        or payload.get("proof_fresh") is not True
    ):
        return None
    required = ("session_id", "account_id", "caller_transport_binding", "capability_id")
    if any(not isinstance(payload.get(key), str) or not payload[key] for key in required):
        return None
    return payload


def context_from_capability(
    token: str, *, audience: str, actions: Iterable[str]
) -> verification.AuthContext | None:
    payload = _payload(token, audience=audience, actions=actions)
    if payload is None:
        return None
    return verification._issue_auth_context(
        session_id=payload["session_id"],
        account_id=payload["account_id"],
        caller_transport_binding=payload["caller_transport_binding"],
        auth_revision=int(payload.get("auth_revision") or 0),
        action=payload["action"],
        expires_at=datetime.fromtimestamp(payload["exp"], timezone.utc),
        capability_id=payload["capability_id"],
        account_status=str(payload.get("account_status") or ""),
        owner_authenticated=True,
        proof_fresh=True,
        auth_level=payload.get("auth_level"),
    )


def denied(code: str = "invalid_service_capability") -> dict[str, Any]:
    return {"ok": False, "state": "denied", "code": code}
