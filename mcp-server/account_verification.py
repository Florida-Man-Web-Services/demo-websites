"""Server-controlled verification and confirmation boundaries.

The lifecycle service is intentionally separate from voice/model plumbing.  A
call handler creates the typed records here, captures private inputs into an
opaque in-memory reference, and passes only references to this module.  OTPs,
phone numbers, and keypad values are never returned from lifecycle APIs or
written to logs/audit rows.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Protocol

log = logging.getLogger("mcp-server.account_verification")


@dataclass(frozen=True, slots=True)
class AuthContext:
    """Typed, action-bound server authentication context."""

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
    forced_mode: bool = False

    def has_capability(self, action: str) -> bool:
        return (
            self.action == action
            and isinstance(self.capability_id, str)
            and bool(self.capability_id.strip())
        )


@dataclass(frozen=True, slots=True)
class TrustedInputEvent:
    """Server-created event for private keypad/consent input."""

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
        return (
            self.action == action
            and isinstance(self.capability_id, str)
            and bool(self.capability_id.strip())
        )


def is_auth_context(value: Any) -> bool:
    return type(value) is AuthContext


def is_trusted_input_event(value: Any) -> bool:
    return type(value) is TrustedInputEvent


class OTPSender(Protocol):
    def send(self, destination: str, message: str, **kwargs: Any) -> Any: ...


class FakeOTPAdapter:
    """Deterministic-injection adapter for tests; never used implicitly."""

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    def send(self, destination: str, message: str, **kwargs: Any) -> bool:
        # The code is kept only inside the injected test adapter.  It does not
        # enter an account-verification result, audit event, or normal log.
        self.sent.append(
            {
                "destination": destination,
                "message": message,
                "code": str(kwargs.get("code") or message),
                "purpose": str(kwargs.get("purpose") or ""),
                "challenge_id": str(kwargs.get("challenge_id") or ""),
            }
        )
        return True

    def last_code_for_test(self) -> str:
        if not self.sent:
            raise AssertionError("no fake verification message was sent")
        return self.sent[-1]["code"]


_ELIGIBLE = frozenset({"paid", "active_owner"})
_ACTIONS = frozenset(
    {
        "client_page_create",
        "client_page_remove",
        "trusted_phone_add",
        "trusted_phone_remove",
    }
)
_PURPOSES = frozenset({"owner_step_up", "destination_phone"})
_E164_RE = re.compile(r"\A\+[1-9][0-9]{9,14}\Z")
_REF_RE = re.compile(r"\A(?:secret|challenge|destination|confirmation)_[A-Za-z0-9_-]{16,128}\Z")

# These are deliberately process-local: raw private input is short-lived and
# never persisted.  OTP verifier digests in SQLite are keyed, so database rows
# are useless without this server-side key.
_VERIFIER_KEY = secrets.token_bytes(32)
_PRIVATE_INPUTS: dict[str, dict[str, Any]] = {}
_DESTINATIONS: dict[str, dict[str, Any]] = {}
_READBACKS: dict[str, dict[str, Any]] = {}
_PRIVATE_LOCK = threading.RLock()
_OTP_ADAPTER: OTPSender | None = None
_SEND_HISTORY: dict[tuple[Any, ...], list[float]] = {}
_USED_EVENTS: set[str] = set()


def _enabled() -> bool:
    return os.getenv("ACCOUNT_LIFECYCLE_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def lifecycle_enabled() -> bool:
    """Expose the default-off gate to trusted voice code."""

    return _enabled()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _parse_time(value: datetime | str | None) -> datetime | None:
    if isinstance(value, datetime):
        out = value
    elif isinstance(value, str):
        try:
            out = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return out.replace(tzinfo=timezone.utc) if out.tzinfo is None else out


def _ttl(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _result(state: str, **fields: Any) -> dict[str, Any]:
    allowed = {
        "code",
        "challenge_id",
        "operation_id",
        "destination_ref",
        "readback_digest",
        "expires_at",
        "retry_after_s",
        "to_last4",
        "auth",
        "action",
        "purpose",
        "attempts",
    }
    out: dict[str, Any] = {"ok": state in {"verification_required", "awaiting_confirmation", "verified_success"}, "state": state}
    for key, value in fields.items():
        if key in allowed and value is not None and (key == "auth" or isinstance(value, (str, int, bool))):
            out[key] = value
    return out


def _deny(code: str) -> dict[str, Any]:
    return _result("denied", code=code)


def _auth_binding(value: AuthContext) -> tuple[str, str, str, int, str, str]:
    return (
        value.session_id,
        value.account_id,
        value.caller_transport_binding,
        value.auth_revision,
        value.action,
        value.capability_id,
    )


def _valid_auth(ctx: Any, action: str | None = None, *, owner: bool = False) -> bool:
    if not is_auth_context(ctx):
        return False
    if any(not isinstance(getattr(ctx, key, None), str) or not getattr(ctx, key).strip() for key in (
        "session_id", "account_id", "caller_transport_binding", "capability_id"
    )):
        return False
    if isinstance(ctx.auth_revision, bool) or not isinstance(ctx.auth_revision, int) or ctx.auth_revision < 0:
        return False
    if ctx.account_status not in _ELIGIBLE or ctx.legacy_auth or ctx.forced_mode:
        return False
    if action is not None and not ctx.has_capability(action):
        return False
    if owner and (ctx.owner_authenticated is not True or ctx.proof_fresh is not True):
        return False
    expiry = _parse_time(ctx.expires_at)
    return expiry is not None and expiry > _now()


def _validate_event(event: Any, auth: AuthContext, event_type: str) -> bool:
    if not is_trusted_input_event(event) or not _valid_auth(auth, auth.action):
        return False
    if event.event_type != event_type or event.has_capability(auth.action) is not True:
        return False
    if _auth_binding(auth) != _auth_binding(
        AuthContext(
            event.session_id, event.account_id, event.caller_transport_binding,
            event.auth_revision, event.action, event.expires_at, event.capability_id,
            auth.account_status, auth.owner_authenticated, auth.proof_fresh,
            auth.auth_level, auth.legacy_auth, auth.forced_mode,
        )
    ):
        return False
    expiry = _parse_time(event.expires_at)
    return expiry is not None and expiry > _now()


def _customers():
    import customers

    return customers


def _trusted_destination(account_id: str) -> str | None:
    customers = _customers()
    with customers._lock:  # noqa: SLF001 - same registry boundary as lifecycle writes
        data = customers._read()  # noqa: SLF001
        rows = customers._lifecycle_account_rows(data, account_id)  # noqa: SLF001
        if len(rows) != 1 or rows[0][1].get("status") not in _ELIGIBLE:
            return None
        map_key, row = rows[0]
        candidates = [
            row.get("current_verification_phone"),
            row.get("verification_phone"),
            map_key,
            row.get("phone"),
        ]
        for candidate in candidates:
            normalized = customers.normalize_phone(candidate)
            if normalized and _E164_RE.fullmatch(normalized):
                return normalized
    return None


def set_otp_adapter(adapter: OTPSender | None) -> None:
    """Inject a fake sender in tests or an explicitly configured production sender."""

    global _OTP_ADAPTER
    _OTP_ADAPTER = adapter
    # A test-injected adapter denotes a fresh isolated delivery boundary.  Do
    # not carry process-local throttle/input state between fake sessions.
    if isinstance(adapter, FakeOTPAdapter) or adapter is None:
        with _PRIVATE_LOCK:
            _SEND_HISTORY.clear()
            _USED_EVENTS.clear()


# Friendly aliases used by integration tests and callers.
set_verification_sender = set_otp_adapter
set_sms_adapter = set_otp_adapter


def _store():
    # Import lazily: account_lifecycle imports our typed records at module load.
    import account_lifecycle

    conn = account_lifecycle._open_store()  # noqa: SLF001
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS verification_challenges (
            challenge_id TEXT PRIMARY KEY,
            operation_id TEXT,
            account_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            caller_transport_binding TEXT NOT NULL,
            auth_revision INTEGER NOT NULL,
            action TEXT NOT NULL,
            purpose TEXT NOT NULL,
            destination_ref TEXT,
            verifier_digest TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            resend_after TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL,
            send_count INTEGER NOT NULL DEFAULT 1,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            consumed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_verification_challenge_binding
            ON verification_challenges(account_id, session_id, purpose, state);
        CREATE TABLE IF NOT EXISTS confirmation_tokens (
            token_digest TEXT PRIMARY KEY,
            operation_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            action TEXT NOT NULL,
            payload_digest TEXT NOT NULL,
            page_version TEXT,
            phone_ref TEXT,
            auth_revision INTEGER NOT NULL,
            expires_at TEXT NOT NULL,
            state TEXT NOT NULL,
            event_id TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            consumed_at TEXT
        );
        """
    )
    return conn


def _keyed_verifier(challenge_id: str, purpose: str, binding: str, code: str) -> str:
    message = "\0".join((challenge_id, purpose, binding, code)).encode()
    return hmac.new(_VERIFIER_KEY, message, hashlib.sha256).hexdigest()


def _secret_ref() -> str:
    return "secret_" + secrets.token_urlsafe(24)


def capture_secret_input(secret: str, *, auth: AuthContext, purpose: str, challenge_id: str | None = None) -> str:
    """Capture private telephony input and return only an opaque server ref."""

    if not _enabled() or not _valid_auth(auth):
        raise ValueError("private input unavailable")
    if not isinstance(secret, str) or not secret or purpose not in _PURPOSES:
        raise ValueError("private input unavailable")
    ref = _secret_ref()
    with _PRIVATE_LOCK:
        _PRIVATE_INPUTS[ref] = {
            "secret": secret,
            "binding": _auth_binding(auth),
            "purpose": purpose,
            "challenge_id": challenge_id,
            "expires_at": _now().timestamp() + _ttl("ACCOUNT_LIFECYCLE_SECRET_TTL_S", 120),
        }
    return ref


def _take_secret(ref: str, auth: AuthContext, purpose: str, challenge_id: str | None = None) -> str | None:
    if not isinstance(ref, str) or not _REF_RE.fullmatch(ref):
        return None
    with _PRIVATE_LOCK:
        item = _PRIVATE_INPUTS.get(ref)
        if item is None:
            return None
        if item["binding"] != _auth_binding(auth) or item["purpose"] != purpose:
            return None
        if challenge_id is not None and item.get("challenge_id") not in (None, challenge_id):
            return None
        if item["expires_at"] <= _now().timestamp():
            _PRIVATE_INPUTS.pop(ref, None)
            return None
        _PRIVATE_INPUTS.pop(ref, None)
        return item["secret"]


def _send(phone: str, code: str, *, purpose: str, challenge_id: str) -> bool:
    adapter = _OTP_ADAPTER
    if adapter is None:
        # No debug-code escape hatch exists on this boundary.  Production with
        # no configured sender fails closed.
        return False
    try:
        if isinstance(adapter, FakeOTPAdapter):
            adapter.send(phone, "verification code", code=code, purpose=purpose, challenge_id=challenge_id)
        else:
            adapter.send(phone, "Your verification code is valid for a short time.", purpose=purpose, challenge_id=challenge_id)
        return True
    except Exception:  # do not expose provider error or secret input
        log.warning("verification sender unavailable purpose=%s", purpose)
        return False


def _throttled(account_id: str, session_id: str, purpose: str) -> tuple[bool, int]:
    now = time.time()
    window = _ttl("ACCOUNT_LIFECYCLE_SEND_WINDOW_S", 3600)
    limit = _ttl("ACCOUNT_LIFECYCLE_SEND_LIMIT", 5)
    cooldown = _ttl("ACCOUNT_LIFECYCLE_RESEND_COOLDOWN_S", 30)
    retry = 0
    with _PRIVATE_LOCK:
        for key in (("account", account_id), ("session", session_id)):
            values = [stamp for stamp in _SEND_HISTORY.get(key, []) if stamp > now - window]
            _SEND_HISTORY[key] = values
            if len(values) >= limit:
                retry = max(retry, int(max(1, values[0] + window - now)))
        # Switching from owner proof to destination proof is a distinct
        # purpose; resend cooldown applies only to the same purpose.
        recent = _SEND_HISTORY.get(("session-purpose", session_id, purpose), [])
        if recent and now - recent[-1] < cooldown:
            retry = max(retry, int(max(1, cooldown - (now - recent[-1]))))
        if retry:
            return True, retry
        for key in (("account", account_id), ("session", session_id), ("session-purpose", session_id, purpose)):
            _SEND_HISTORY.setdefault(key, []).append(now)
    return False, 0


def _issue_challenge(
    *, auth: AuthContext, purpose: str, operation_id: str | None = None,
    destination_ref: str | None = None,
) -> dict[str, Any]:
    if not _enabled():
        return _deny("feature_disabled")
    if purpose not in _PURPOSES or not _valid_auth(auth, auth.action):
        return _deny("invalid_context")
    if purpose == "destination_phone" and not _valid_auth(auth, "trusted_phone_add", owner=True):
        return _deny("verification_required")
    if purpose == "owner_step_up" and auth.action not in _ACTIONS:
        return _deny("invalid_context")
    phone = _trusted_destination(auth.account_id) if purpose == "owner_step_up" else None
    if purpose == "destination_phone":
        with _PRIVATE_LOCK:
            destination = _DESTINATIONS.get(destination_ref or "")
        if destination is None or destination["binding"] != _auth_binding(auth):
            return _deny("destination_required")
        phone = destination["phone"]
    if not phone:
        return _deny("account_unavailable")
    limited, retry = _throttled(auth.account_id, auth.session_id, purpose)
    if limited:
        return _result("denied", code="throttled", retry_after_s=retry)
    challenge_id = "challenge_" + secrets.token_urlsafe(24)
    code = f"{secrets.randbelow(1_000_000):06d}"
    expiry = _now() + __import__("datetime").timedelta(seconds=_ttl("ACCOUNT_LIFECYCLE_OTP_TTL_S", 300))
    conn = _store()
    try:
        conn.execute(
            """INSERT INTO verification_challenges
            (challenge_id, operation_id, account_id, session_id, caller_transport_binding,
             auth_revision, action, purpose, destination_ref, verifier_digest, expires_at,
             resend_after, max_attempts, state, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (
                challenge_id, operation_id, auth.account_id, auth.session_id,
                auth.caller_transport_binding, auth.auth_revision, auth.action, purpose,
                destination_ref, _keyed_verifier(challenge_id, purpose, auth.session_id, code),
                _iso(expiry), _iso(_now() + __import__("datetime").timedelta(seconds=_ttl("ACCOUNT_LIFECYCLE_RESEND_COOLDOWN_S", 30))),
                _ttl("ACCOUNT_LIFECYCLE_MAX_ATTEMPTS", 5), _iso(_now()),
            ),
        )
        if not _send(phone, code, purpose=purpose, challenge_id=challenge_id):
            conn.execute("UPDATE verification_challenges SET state='failed' WHERE challenge_id=?", (challenge_id,))
            conn.commit()
            return _result("failed", code="sender_unavailable")
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return _result("failed", code="storage_unavailable")
    finally:
        conn.close()
    return _result(
        "verification_required", challenge_id=challenge_id, purpose=purpose,
        action=auth.action, expires_at=_iso(expiry),
        to_last4="••••" + phone[-4:],
    )


def begin_step_up(*, action: str, ctx: AuthContext) -> dict[str, Any]:
    """Start fresh owner proof for exactly one lifecycle action."""

    if action not in _ACTIONS or not _valid_auth(ctx, action):
        return _deny("invalid_context") if _enabled() else _deny("feature_disabled")
    return _issue_challenge(auth=ctx, purpose="owner_step_up")


def request_owner_verification(*, auth: AuthContext, purpose: str, operation_id: str | None = None) -> dict[str, Any]:
    if purpose != "owner_step_up":
        return _deny("invalid_purpose")
    return _issue_challenge(auth=auth, purpose=purpose, operation_id=operation_id)


def _complete_challenge(
    *, challenge_id: str, secret_input_ref: str, ctx: AuthContext,
    purpose: str, operation_id: str | None = None,
) -> dict[str, Any]:
    if not _enabled():
        return _deny("feature_disabled")
    if not isinstance(challenge_id, str) or not challenge_id.startswith("challenge_"):
        return _deny("invalid_challenge")
    conn = _store()
    try:
        row = conn.execute("SELECT * FROM verification_challenges WHERE challenge_id=?", (challenge_id,)).fetchone()
        if row is None:
            return _deny("invalid_challenge")
        if not _valid_auth(ctx, row["action"]) or row["purpose"] != purpose:
            return _deny("invalid_context")
        if row["account_id"] != ctx.account_id or row["session_id"] != ctx.session_id or row["caller_transport_binding"] != ctx.caller_transport_binding:
            return _deny("invalid_context")
        if row["operation_id"] != operation_id and (operation_id is not None or row["operation_id"] is not None):
            return _deny("wrong_operation")
        with _PRIVATE_LOCK:
            private_item = _PRIVATE_INPUTS.get(secret_input_ref)
        if private_item is not None and private_item.get("binding") == _auth_binding(ctx) and private_item.get("purpose") != purpose:
            return _deny("wrong_purpose")
        if row["state"] != "pending":
            return _deny("replayed_challenge")
        if _parse_time(row["expires_at"]) is None or _parse_time(row["expires_at"]) <= _now():
            conn.execute("UPDATE verification_challenges SET state='expired' WHERE challenge_id=?", (challenge_id,))
            conn.commit()
            return _deny("expired")
        secret = _take_secret(secret_input_ref, ctx, purpose, challenge_id)
        if secret is None:
            return _deny("secret_unavailable")
        normalized = "".join(ch for ch in secret if ch.isdigit())
        attempts = int(row["attempts"]) + 1
        if attempts > int(row["max_attempts"]):
            conn.execute("UPDATE verification_challenges SET state='exhausted', attempts=? WHERE challenge_id=?", (attempts, challenge_id))
            conn.commit()
            return _deny("attempts_exhausted")
        expected = _keyed_verifier(challenge_id, purpose, ctx.session_id, normalized)
        if not hmac.compare_digest(row["verifier_digest"], expected):
            state = "exhausted" if attempts >= int(row["max_attempts"]) else "pending"
            conn.execute("UPDATE verification_challenges SET attempts=?, state=? WHERE challenge_id=?", (attempts, state, challenge_id))
            conn.commit()
            return _deny("attempts_exhausted" if state == "exhausted" else "invalid_code")
        conn.execute("UPDATE verification_challenges SET attempts=?, state='consumed', consumed_at=? WHERE challenge_id=?", (attempts, _iso(_now()), challenge_id))
        conn.commit()
    finally:
        conn.close()
    if purpose == "owner_step_up":
        fresh = replace(ctx, owner_authenticated=True, proof_fresh=True, auth_level="step_up")
        return _result("verified_success", challenge_id=challenge_id, auth=fresh)
    return _result("verified_success", challenge_id=challenge_id, operation_id=operation_id)


def complete_step_up(*, challenge_id: str, secret_input_ref: str, ctx: AuthContext) -> dict[str, Any]:
    return _complete_challenge(
        challenge_id=challenge_id, secret_input_ref=secret_input_ref,
        ctx=ctx, purpose="owner_step_up",
    )


def capture_account_phone(*, auth: AuthContext, secret_input_ref: str) -> dict[str, Any]:
    """Capture a destination number through a private, non-model event."""

    if not _enabled():
        return _deny("feature_disabled")
    if not _valid_auth(auth, "trusted_phone_add", owner=True):
        return _deny("verification_required")
    raw = _take_secret(secret_input_ref, auth, "destination_phone")
    if raw is None:
        return _deny("secret_unavailable")
    phone = _customers().normalize_phone(raw)
    if not phone or not _E164_RE.fullmatch(phone):
        return _deny("invalid_destination")
    ref = "destination_" + secrets.token_urlsafe(24)
    with _PRIVATE_LOCK:
        _DESTINATIONS[ref] = {
            "phone": phone,
            "binding": _auth_binding(auth),
            "auth": auth,
            "expires_at": _now().timestamp() + _ttl("ACCOUNT_LIFECYCLE_DESTINATION_TTL_S", 600),
            "verified": False,
        }
    return _result("verified_success", destination_ref=ref)


def request_destination_verification(*, auth: AuthContext, operation_id: str, send_consent_event: TrustedInputEvent | None) -> dict[str, Any]:
    if not _enabled():
        return _deny("feature_disabled")
    if not _valid_auth(auth, "trusted_phone_add", owner=True):
        return _deny("verification_required")
    if not isinstance(operation_id, str) or not operation_id:
        return _deny("invalid_operation")
    if send_consent_event is None or not _validate_event(send_consent_event, auth, "send_consent"):
        return _deny("consent_required")
    with _PRIVATE_LOCK:
        refs = [ref for ref, item in _DESTINATIONS.items() if item["binding"] == _auth_binding(auth) and item["expires_at"] > _now().timestamp()]
    if not refs:
        return _deny("destination_required")
    return _issue_challenge(auth=auth, purpose="destination_phone", operation_id=operation_id, destination_ref=refs[-1])


def verify_destination_challenge(*, auth: AuthContext, operation_id: str, challenge_id: str, secret_input_ref: str) -> dict[str, Any]:
    if not _enabled():
        return _deny("feature_disabled")
    if not _valid_auth(auth, "trusted_phone_add", owner=True):
        return _deny("verification_required")
    result = _complete_challenge(
        challenge_id=challenge_id, secret_input_ref=secret_input_ref,
        ctx=auth, purpose="destination_phone", operation_id=operation_id,
    )
    if result.get("state") == "verified_success":
        conn = _store()
        try:
            row = conn.execute("SELECT destination_ref FROM verification_challenges WHERE challenge_id=?", (challenge_id,)).fetchone()
        finally:
            conn.close()
        destination_ref = row["destination_ref"] if row else None
        with _PRIVATE_LOCK:
            item = _DESTINATIONS.get(destination_ref or "")
            if item is not None:
                item["verified"] = True
        return _result("verified_success", challenge_id=challenge_id, operation_id=operation_id, destination_ref=destination_ref)
    return result


def _operation_binding(auth: AuthContext, operation_id: str) -> dict[str, Any] | None:
    import account_lifecycle

    conn = account_lifecycle._try_open_store()  # noqa: SLF001
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT operation_id, account_id, action, payload_digest, expected_auth_revision, expected_page_version, phone_ref, state, expires_at FROM operations WHERE operation_id=? AND account_id=?",
            (operation_id, auth.account_id),
        ).fetchone()
        if row is None or not _valid_auth(auth, row["action"], owner=True):
            return None
        if row["expected_auth_revision"] != auth.auth_revision or row["state"] not in {"awaiting_confirmation", "pending"}:
            return None
        return {key: row[key] for key in row.keys()}
    finally:
        conn.close()


def get_confirmation_readback(*, auth: AuthContext, operation_id: str) -> dict[str, Any]:
    if not _enabled():
        return _deny("feature_disabled")
    if not isinstance(operation_id, str):
        return _deny("invalid_operation")
    binding = _operation_binding(auth, operation_id)
    if binding is None:
        return _deny("invalid_context")
    canonical = json.dumps(
        {
            "operation_id": binding["operation_id"],
            "action": binding["action"],
            "payload_digest": binding["payload_digest"],
            "page_version": binding["expected_page_version"],
            "phone_ref": binding["phone_ref"],
            "account_id": auth.account_id,
            "auth_revision": auth.auth_revision,
        }, sort_keys=True, separators=(",", ":"),
    ).encode()
    digest = hashlib.sha256(canonical).hexdigest()
    with _PRIVATE_LOCK:
        _READBACKS[(auth.session_id + "\0" + operation_id)] = {
            "digest": digest,
            "binding": binding,
            "expires_at": min(_parse_time(auth.expires_at).timestamp(), _now().timestamp() + _ttl("ACCOUNT_LIFECYCLE_CONFIRMATION_TTL_S", 120)),
        }
    return _result("awaiting_confirmation", operation_id=operation_id, action=binding["action"], readback_digest=digest, expires_at=_iso(datetime.fromtimestamp(_READBACKS[(auth.session_id + "\0" + operation_id)]["expires_at"], timezone.utc)))


def capture_lifecycle_confirmation(*, auth: AuthContext, operation_id: str, readback_digest: str, event: TrustedInputEvent) -> str | dict[str, Any]:
    if not _enabled():
        return _deny("feature_disabled")
    if not _valid_auth(auth, auth.action, owner=True) or not _validate_event(event, auth, "keypad_confirm"):
        return _deny("invalid_confirmation_event")
    if not isinstance(readback_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", readback_digest):
        return _deny("invalid_readback")
    if event.value_digest is None or not re.fullmatch(r"[a-f0-9]{64}", event.value_digest):
        return _deny("invalid_keypad_event")
    key = auth.session_id + "\0" + operation_id
    with _PRIVATE_LOCK:
        readback = _READBACKS.get(key)
        if readback is None or readback["expires_at"] <= _now().timestamp() or readback["digest"] != readback_digest:
            return _deny("readback_mismatch")
        current_binding = _operation_binding(auth, operation_id)
        if current_binding is None or current_binding != readback["binding"]:
            return _deny("readback_mismatch")
        if event.event_id in _USED_EVENTS:
            return _deny("event_replayed")
        _USED_EVENTS.add(event.event_id)
    binding = readback["binding"]
    token = "confirmation_" + secrets.token_urlsafe(32)
    token_digest = hashlib.sha256(token.encode()).hexdigest()
    conn = _store()
    try:
        conn.execute(
            """INSERT INTO confirmation_tokens
            (token_digest, operation_id, account_id, session_id, action, payload_digest,
             page_version, phone_ref, auth_revision, expires_at, state, event_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (
                token_digest, operation_id, auth.account_id, auth.session_id, binding["action"],
                binding["payload_digest"], str(binding["expected_page_version"] or ""),
                binding["phone_ref"], auth.auth_revision,
                _iso(datetime.fromtimestamp(readback["expires_at"], timezone.utc)), event.event_id, _iso(_now()),
            ),
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return _deny("confirmation_unavailable")
    finally:
        conn.close()
    try:
        import account_lifecycle

        bound = account_lifecycle.bind_operation_confirmation(
            ctx=auth, operation_id=operation_id, confirmation_digest=hashlib.sha256(token.encode()).hexdigest()
        )
        if bound.get("state") != "awaiting_confirmation":
            return _deny("confirmation_unavailable")
    except Exception:
        return _deny("confirmation_unavailable")
    return token


__all__ = [
    "AuthContext",
    "TrustedInputEvent",
    "FakeOTPAdapter",
    "is_auth_context",
    "is_trusted_input_event",
    "lifecycle_enabled",
    "set_otp_adapter",
    "set_verification_sender",
    "set_sms_adapter",
    "capture_secret_input",
    "begin_step_up",
    "complete_step_up",
    "request_owner_verification",
    "capture_account_phone",
    "request_destination_verification",
    "verify_destination_challenge",
    "get_confirmation_readback",
    "capture_lifecycle_confirmation",
]
