"""Default-off SQLite client-account lifecycle store.

The store is deliberately separate from ``customers.py`` and
``personal_pages.py``.  It owns only lifecycle intent/page state and audit
metadata; account identity and authentication remain server-owned inputs from
later tasks.  The context contract accepted here is intentionally explicit so
model-supplied account, caller, confirmation, or session fields cannot become
authority by accident.

No audit row or result includes raw phone numbers, OTPs, full page bodies, or
secret input.  Page bodies are retained only in the private SQLite page/intent
records needed to render or commit a page.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import account_pages

SAFE_STATES = frozenset(
    {
        "denied",
        "verification_required",
        "awaiting_confirmation",
        "pending",
        "verified_success",
        "verified_noop",
        "failed",
    }
)
ELIGIBLE_ACCOUNT_STATUSES = frozenset({"paid", "active_owner"})
FINAL_OPERATION_STATES = frozenset({"verified_success", "verified_noop", "cancelled", "failed"})
_PAGE_ACTIONS = frozenset({"client_page_create", "client_page_remove"})
_PHONE_ACTIONS = frozenset({"trusted_phone_add", "trusted_phone_remove"})
_SAFE_FIELD_NAMES = frozenset(
    {
        "operation_id",
        "page_id",
        "slug",
        "action",
        "state",
        "code",
        "reason",
        "payload_digest",
        "confirmation_digest",
        "expected_auth_revision",
        "page_version",
        "expires_at",
        "cache_control",
        "expected_state",
        "public_status",
        "enabled",
    }
)
_SLUG_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_IDEMPOTENCY_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_OPAQUE_REF_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


@dataclass(frozen=True)
class LifecycleContext:
    """Server-created, action-bound proof passed into lifecycle functions.

    A later verification module may use its own AuthContext type; lifecycle
    functions also accept mappings/objects with these same field names.  The
    two boolean proof fields must be set by the server, never by a model tool.
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().replace(microsecond=0).isoformat()


def _parse_time(value: datetime | str | None) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _value(ctx: Any, key: str, default: Any = None) -> Any:
    if isinstance(ctx, Mapping):
        return ctx.get(key, default)
    return getattr(ctx, key, default)


def _db_path() -> str | Path:
    configured = os.getenv("ACCOUNT_LIFECYCLE_DB")
    if configured:
        return configured
    if Path("/data").is_dir():
        return "/data/account-lifecycle.sqlite3"
    return Path(__file__).resolve().parent.parent / "data" / "account-lifecycle.sqlite3"


def _enabled() -> bool:
    return (os.getenv("ACCOUNT_LIFECYCLE_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"})


def _connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open a connection with WAL, foreign keys, and explicit transactions."""

    raw_path = str(path if path is not None else _db_path())
    if raw_path != ":memory:":
        Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(raw_path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _init_schema(path: str | Path | None = None) -> None:
    """Create the isolated schema; this is safe to call on every operation."""

    conn = _connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS client_pages (
                page_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                operation_id TEXT,
                slug TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                payload_digest TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('draft', 'published', 'tombstoned')),
                page_version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                published_at TEXT,
                deleted_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_client_pages_account
                ON client_pages(account_id);
            CREATE INDEX IF NOT EXISTS idx_client_pages_state_slug
                ON client_pages(state, slug);

            CREATE TABLE IF NOT EXISTS operations (
                operation_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                action TEXT NOT NULL,
                page_id TEXT,
                phone_ref TEXT,
                idempotency_key TEXT NOT NULL,
                payload_digest TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                expected_auth_revision INTEGER NOT NULL,
                expected_page_version INTEGER,
                state TEXT NOT NULL,
                confirmation_digest TEXT,
                expires_at TEXT NOT NULL,
                receipt_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(account_id, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS idx_operations_account
                ON operations(account_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_operations_lookup
                ON operations(operation_id, account_id);

            CREATE TABLE IF NOT EXISTS challenges (
                challenge_id TEXT PRIMARY KEY,
                operation_id TEXT,
                account_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                verifier_digest TEXT,
                expires_at TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_challenges_operation
                ON challenges(operation_id);
            CREATE INDEX IF NOT EXISTS idx_challenges_account
                ON challenges(account_id, state);

            CREATE TABLE IF NOT EXISTS audit_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT,
                account_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                opaque_ref TEXT,
                payload_digest TEXT,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_audit_operation
                ON audit_events(operation_id, event_id);
            CREATE INDEX IF NOT EXISTS idx_audit_account
                ON audit_events(account_id, event_id);
            """
        )
    finally:
        conn.close()


def _safe_result(state: str, **fields: Any) -> dict[str, Any]:
    """Build a result safe to expose to a model or voice transcript.

    Unknown fields are dropped rather than copied.  This fail-closed behavior
    protects against accidentally adding raw secret/page fields in a future
    caller.
    """

    if state not in SAFE_STATES:
        state = "failed"
    result: dict[str, Any] = {
        "ok": state in {"awaiting_confirmation", "pending", "verified_success", "verified_noop"},
        "state": state,
    }
    for key, value in fields.items():
        if key not in _SAFE_FIELD_NAMES or value is None:
            continue
        if isinstance(value, (str, int, bool)):
            result[key] = value
    return result


def _denied(code: str) -> dict[str, Any]:
    return _safe_result("denied", code=code)


def _invalid_context(ctx: Any, action: str) -> bool:
    if ctx is None:
        return True
    required = (
        "session_id",
        "account_id",
        "caller_transport_binding",
        "capability_id",
    )
    if any(not isinstance(_value(ctx, key), str) or not _value(ctx, key).strip() for key in required):
        return True
    auth_revision = _value(ctx, "auth_revision")
    if isinstance(auth_revision, bool) or not isinstance(auth_revision, int) or auth_revision < 0:
        return True
    if _value(ctx, "action") != action:
        return True
    if _value(ctx, "account_status") not in ELIGIBLE_ACCOUNT_STATUSES:
        return True
    if _value(ctx, "owner_authenticated") is not True or _value(ctx, "proof_fresh") is not True:
        return True
    if _value(ctx, "auth_level") in {"cid_legacy", "soft", "cid_only"} or _value(ctx, "legacy_auth") is True:
        return True
    expires_at = _parse_time(_value(ctx, "expires_at"))
    return expires_at is None or expires_at <= _now()


def _guard(ctx: Any, action: str) -> dict[str, Any] | None:
    if not _enabled():
        return _denied("feature_disabled")
    if _invalid_context(ctx, action):
        return _denied("invalid_context")
    return None


def _validate_idempotency(key: str) -> bool:
    return isinstance(key, str) and bool(_IDEMPOTENCY_RE.fullmatch(key))


def _validate_ref(value: str) -> bool:
    return isinstance(value, str) and bool(_OPAQUE_REF_RE.fullmatch(value))


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(16)}"


def _open_store() -> sqlite3.Connection:
    path = _db_path()
    _init_schema(path)
    return _connect(path)


def _audit(
    conn: sqlite3.Connection,
    *,
    operation_id: str | None,
    account_id: str,
    event_type: str,
    opaque_ref: str | None = None,
    payload_digest: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    safe_metadata = {
        key: value
        for key, value in (metadata or {}).items()
        if key in {"action", "state", "page_version", "expected_auth_revision", "reason"}
        and isinstance(value, (str, int, bool))
    }
    conn.execute(
        """INSERT INTO audit_events
           (operation_id, account_id, event_type, opaque_ref, payload_digest,
            metadata_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            operation_id,
            account_id,
            event_type,
            opaque_ref,
            payload_digest,
            json.dumps(safe_metadata, sort_keys=True),
            _now_iso(),
        ),
    )


def _operation_result(row: sqlite3.Row) -> dict[str, Any]:
    internal_state = str(row["state"])
    public_state = "verified_noop" if internal_state == "cancelled" else internal_state
    fields: dict[str, Any] = {
        "operation_id": row["operation_id"],
        "action": row["action"],
        "payload_digest": row["payload_digest"],
        "expected_auth_revision": row["expected_auth_revision"],
        "expires_at": row["expires_at"],
    }
    if row["page_id"]:
        fields["page_id"] = row["page_id"]
    try:
        receipt = json.loads(row["receipt_json"] or "{}")
    except json.JSONDecodeError:
        receipt = {}
    for key in ("page_id", "slug", "public_status", "page_version", "code"):
        if key in receipt:
            fields[key] = receipt[key]
    if internal_state == "cancelled":
        fields["code"] = "cancelled"
    return _safe_result(public_state, **fields)


def _existing_operation(
    conn: sqlite3.Connection, account_id: str, idempotency_key: str, payload_digest: str
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM operations WHERE account_id = ? AND idempotency_key = ?",
        (account_id, idempotency_key),
    ).fetchone()
    if row is None:
        return None
    if row["payload_digest"] != payload_digest:
        return _denied("idempotency_conflict")
    return _operation_result(row)


def _create_operation(
    *,
    ctx: Any,
    action: str,
    idempotency_key: str,
    payload_digest: str,
    payload: Mapping[str, Any],
    page_id: str | None = None,
    phone_ref: str | None = None,
    expected_page_version: int | None = None,
) -> dict[str, Any]:
    guard = _guard(ctx, action)
    if guard:
        return guard
    if not _validate_idempotency(idempotency_key):
        return _denied("invalid_idempotency_key")
    conn = _open_store()
    operation_id = _new_id("op")
    now = _now_iso()
    expires = (_now() + timedelta(minutes=10)).replace(microsecond=0).isoformat()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = _existing_operation(conn, _value(ctx, "account_id"), idempotency_key, payload_digest)
        if existing:
            conn.execute("COMMIT")
            return existing
        conn.execute(
            """INSERT INTO operations
               (operation_id, account_id, action, page_id, phone_ref,
                idempotency_key, payload_digest, payload_json,
                expected_auth_revision, expected_page_version, state,
                expires_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'awaiting_confirmation', ?, ?, ?)""",
            (
                operation_id,
                _value(ctx, "account_id"),
                action,
                page_id,
                phone_ref,
                idempotency_key,
                payload_digest,
                json.dumps(dict(payload), sort_keys=True, ensure_ascii=False),
                _value(ctx, "auth_revision"),
                expected_page_version,
                expires,
                now,
                now,
            ),
        )
        _audit(
            conn,
            operation_id=operation_id,
            account_id=_value(ctx, "account_id"),
            event_type="prepared",
            opaque_ref=operation_id,
            payload_digest=payload_digest,
            metadata={
                "action": action,
                "state": "awaiting_confirmation",
                "expected_auth_revision": _value(ctx, "auth_revision"),
            },
        )
        conn.execute("COMMIT")
    except sqlite3.IntegrityError:
        conn.execute("ROLLBACK")
        with _open_store() as retry_conn:
            existing = _existing_operation(
                retry_conn, _value(ctx, "account_id"), idempotency_key, payload_digest
            )
        return existing or _safe_result("failed", code="operation_conflict")
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        return _safe_result("failed", code="storage_unavailable")
    finally:
        conn.close()

    fields = {
        "operation_id": operation_id,
        "action": action,
        "payload_digest": payload_digest,
        "expected_auth_revision": _value(ctx, "auth_revision"),
        "expires_at": expires,
    }
    if page_id:
        fields["page_id"] = page_id
    return _safe_result("awaiting_confirmation", **fields)


def prepare_client_page_create(*, ctx: Any, title: str, body: str, idempotency_key: str) -> dict[str, Any]:
    """Validate and durably prepare a page without publishing it."""

    guard = _guard(ctx, "client_page_create")
    if guard:
        return guard
    known = set(_value(ctx, "known_identifiers", ()) or ())
    known.add(str(_value(ctx, "account_id")))
    validation = account_pages.validate_public_page(title, body, known_identifiers=known)
    if not validation["ok"]:
        return _denied("invalid_page_payload")
    page_id = _new_id("page")
    slug = "p-" + secrets.token_hex(8)
    payload_digest = validation["digest"]
    return _create_operation(
        ctx=ctx,
        action="client_page_create",
        idempotency_key=idempotency_key,
        payload_digest=payload_digest,
        payload={"title": validation["title"], "body": validation["body"], "slug": slug},
        page_id=page_id,
    )


def prepare_client_page_remove(*, ctx: Any, page_id: str, idempotency_key: str) -> dict[str, Any]:
    """Prepare a page tombstone after checking account ownership."""

    guard = _guard(ctx, "client_page_remove")
    if guard:
        return guard
    if not _validate_ref(page_id):
        return _denied("not_found")
    conn = _open_store()
    try:
        row = conn.execute(
            "SELECT page_id, account_id, state, page_version FROM client_pages WHERE page_id = ?",
            (page_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None or row["account_id"] != _value(ctx, "account_id") or row["state"] != "published":
        return _denied("not_found")
    digest = hashlib.sha256(f"client_page_remove\0{page_id}".encode()).hexdigest()
    return _create_operation(
        ctx=ctx,
        action="client_page_remove",
        idempotency_key=idempotency_key,
        payload_digest=digest,
        payload={"page_id": page_id},
        page_id=page_id,
        expected_page_version=row["page_version"],
    )


def prepare_trusted_phone_add(*, ctx: Any, idempotency_key: str) -> dict[str, Any]:
    """Prepare a future verified destination-phone addition without raw input."""

    digest = hashlib.sha256(b"trusted_phone_add").hexdigest()
    return _create_operation(
        ctx=ctx,
        action="trusted_phone_add",
        idempotency_key=idempotency_key,
        payload_digest=digest,
        payload={"action": "trusted_phone_add"},
    )


def prepare_trusted_phone_remove(*, ctx: Any, phone_ref: str, idempotency_key: str) -> dict[str, Any]:
    """Prepare removal using only an opaque phone reference."""

    guard = _guard(ctx, "trusted_phone_remove")
    if guard:
        return guard
    if not _validate_ref(phone_ref) or any(char.isdigit() for char in phone_ref):
        return _denied("invalid_phone_ref")
    digest = hashlib.sha256(f"trusted_phone_remove\0{phone_ref}".encode()).hexdigest()
    return _create_operation(
        ctx=ctx,
        action="trusted_phone_remove",
        idempotency_key=idempotency_key,
        payload_digest=digest,
        payload={"phone_ref": phone_ref},
        phone_ref=phone_ref,
    )


def bind_operation_confirmation(*, ctx: Any, operation_id: str, confirmation_digest: str) -> dict[str, Any]:
    """Bind a server-created keypad token digest before commit.

    This small boundary lets Task 3 create the single-use token without ever
    putting the token in a model-facing result or in the operation payload.
    """

    if not _enabled() or not _validate_ref(operation_id):
        return _denied("invalid_context" if _enabled() else "feature_disabled")
    if not isinstance(confirmation_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", confirmation_digest):
        return _denied("invalid_confirmation")
    conn = _open_store()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM operations WHERE operation_id = ? AND account_id = ?",
            (operation_id, _value(ctx, "account_id")),
        ).fetchone()
        if row is None or _invalid_context(ctx, row["action"]):
            conn.execute("ROLLBACK")
            return _denied("not_found")
        conn.execute(
            "UPDATE operations SET confirmation_digest = ?, updated_at = ? WHERE operation_id = ?",
            (confirmation_digest, _now_iso(), operation_id),
        )
        _audit(
            conn,
            operation_id=operation_id,
            account_id=_value(ctx, "account_id"),
            event_type="confirmation_bound",
            opaque_ref=operation_id,
            payload_digest=row["payload_digest"],
            metadata={"action": row["action"]},
        )
        conn.execute("COMMIT")
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        return _safe_result("failed", code="storage_unavailable")
    finally:
        conn.close()
    return _safe_result("awaiting_confirmation", operation_id=operation_id, confirmation_digest=confirmation_digest)


def commit_account_operation(*, ctx: Any, operation_id: str, confirmation_token: str) -> dict[str, Any]:
    """Commit a confirmed page operation with all checks in one transaction."""

    if not _enabled():
        return _denied("feature_disabled")
    if not _validate_ref(operation_id) or not isinstance(confirmation_token, str):
        return _denied("invalid_confirmation")
    conn = _open_store()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM operations WHERE operation_id = ? AND account_id = ?",
            (operation_id, _value(ctx, "account_id")),
        ).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            return _denied("not_found")
        if row["state"] in FINAL_OPERATION_STATES:
            conn.execute("COMMIT")
            return _operation_result(row)
        if _invalid_context(ctx, row["action"]):
            conn.execute("ROLLBACK")
            return _denied("invalid_context")
        expires_at = _parse_time(row["expires_at"])
        if expires_at is None or expires_at <= _now():
            conn.execute(
                "UPDATE operations SET state = 'failed', updated_at = ? WHERE operation_id = ?",
                (_now_iso(), operation_id),
            )
            _audit(
                conn,
                operation_id=operation_id,
                account_id=row["account_id"],
                event_type="expired",
                opaque_ref=operation_id,
                payload_digest=row["payload_digest"],
                metadata={"action": row["action"], "reason": "expired"},
            )
            conn.execute("COMMIT")
            return _safe_result("failed", operation_id=operation_id, code="expired")
        if row["expected_auth_revision"] != _value(ctx, "auth_revision"):
            conn.execute("ROLLBACK")
            return _denied("stale_auth_revision")
        confirmation_digest = hashlib.sha256(confirmation_token.encode("utf-8")).hexdigest()
        if not row["confirmation_digest"]:
            conn.execute("ROLLBACK")
            return _safe_result("verification_required", operation_id=operation_id, code="confirmation_required")
        if not secrets.compare_digest(row["confirmation_digest"], confirmation_digest):
            conn.execute("ROLLBACK")
            return _denied("invalid_confirmation")

        intent = json.loads(row["payload_json"])
        receipt: dict[str, Any] = {
            "operation_id": operation_id,
            "action": row["action"],
            "payload_digest": row["payload_digest"],
        }
        if row["action"] == "client_page_create":
            page = conn.execute(
                "SELECT * FROM client_pages WHERE page_id = ?", (row["page_id"],)
            ).fetchone()
            if page is not None:
                if page["account_id"] != row["account_id"] or page["payload_digest"] != row["payload_digest"]:
                    conn.execute("ROLLBACK")
                    return _safe_result("failed", operation_id=operation_id, code="page_conflict")
                receipt.update(page_id=page["page_id"], slug=page["slug"], public_status=200)
            else:
                conn.execute(
                    """INSERT INTO client_pages
                       (page_id, account_id, operation_id, slug, title, body,
                        payload_digest, state, page_version, created_at, published_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'published', 1, ?, ?)""",
                    (
                        row["page_id"],
                        row["account_id"],
                        operation_id,
                        intent["slug"],
                        intent["title"],
                        intent["body"],
                        row["payload_digest"],
                        _now_iso(),
                        _now_iso(),
                    ),
                )
                receipt.update(page_id=row["page_id"], slug=intent["slug"], public_status=200, page_version=1)
        elif row["action"] == "client_page_remove":
            page = conn.execute(
                "SELECT * FROM client_pages WHERE page_id = ?", (row["page_id"],)
            ).fetchone()
            if page is None or page["account_id"] != row["account_id"]:
                conn.execute("ROLLBACK")
                return _safe_result("failed", operation_id=operation_id, code="page_not_found")
            if page["state"] == "tombstoned":
                receipt.update(page_id=page["page_id"], slug=page["slug"], public_status=410)
            elif page["state"] != "published" or page["page_version"] != row["expected_page_version"]:
                conn.execute("ROLLBACK")
                return _safe_result("failed", operation_id=operation_id, code="page_changed")
            else:
                conn.execute(
                    "UPDATE client_pages SET state = 'tombstoned', deleted_at = ?, page_version = page_version + 1 WHERE page_id = ?",
                    (_now_iso(), row["page_id"]),
                )
                receipt.update(page_id=page["page_id"], slug=page["slug"], public_status=410, page_version=page["page_version"] + 1)
        else:
            conn.execute("ROLLBACK")
            return _safe_result("failed", operation_id=operation_id, code="unsupported_commit_action")

        conn.execute(
            "UPDATE operations SET state = 'verified_success', receipt_json = ?, updated_at = ? WHERE operation_id = ?",
            (json.dumps(receipt, sort_keys=True), _now_iso(), operation_id),
        )
        _audit(
            conn,
            operation_id=operation_id,
            account_id=row["account_id"],
            event_type="committed",
            opaque_ref=operation_id,
            payload_digest=row["payload_digest"],
            metadata={"action": row["action"], "state": "verified_success"},
        )
        conn.execute("COMMIT")
    except (sqlite3.Error, KeyError, json.JSONDecodeError):
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        return _safe_result("failed", operation_id=operation_id, code="storage_unavailable")
    finally:
        conn.close()

    return _safe_result("verified_success", **receipt)


def get_lifecycle_status(*, ctx: Any, operation_id: str | None = None) -> dict[str, Any]:
    """Return only safe status metadata for one account's operation."""

    if not _enabled():
        return _denied("feature_disabled")
    context_action = _value(ctx, "action")
    allowed_status_actions = _PAGE_ACTIONS | _PHONE_ACTIONS | {
        "lifecycle_status",
        "get_lifecycle_status",
    }
    if context_action not in allowed_status_actions or _invalid_context(ctx, context_action):
        return _denied("invalid_context")
    conn = _open_store()
    try:
        if operation_id:
            row = conn.execute(
                "SELECT * FROM operations WHERE operation_id = ? AND account_id = ?",
                (operation_id, _value(ctx, "account_id")),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM operations WHERE account_id = ? ORDER BY created_at DESC LIMIT 1",
                (_value(ctx, "account_id"),),
            ).fetchone()
    finally:
        conn.close()
    return _operation_result(row) if row else _denied("not_found")


def cancel_account_operation(*, ctx: Any, operation_id: str) -> dict[str, Any]:
    """Cancel an uncommitted operation; cancellation is idempotent."""

    if not _enabled():
        return _denied("feature_disabled")
    if not _validate_ref(operation_id):
        return _denied("not_found")
    conn = _open_store()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM operations WHERE operation_id = ? AND account_id = ?",
            (operation_id, _value(ctx, "account_id")),
        ).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            return _denied("not_found")
        if row["state"] in FINAL_OPERATION_STATES:
            conn.execute("COMMIT")
            return _operation_result(row)
        if _invalid_context(ctx, row["action"]):
            conn.execute("ROLLBACK")
            return _denied("invalid_context")
        conn.execute(
            "UPDATE operations SET state = 'cancelled', updated_at = ? WHERE operation_id = ?",
            (_now_iso(), operation_id),
        )
        _audit(
            conn,
            operation_id=operation_id,
            account_id=row["account_id"],
            event_type="cancelled",
            opaque_ref=operation_id,
            payload_digest=row["payload_digest"],
            metadata={"action": row["action"], "state": "cancelled"},
        )
        conn.execute("COMMIT")
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        return _safe_result("failed", code="storage_unavailable")
    finally:
        conn.close()
    return _safe_result("verified_noop", operation_id=operation_id, code="cancelled")


def get_public_page(slug: str) -> tuple[int, dict[str, Any], str]:
    """Read exactly one page with a cache-busting response policy.

    Unknown and unpublished pages are indistinguishable.  Tombstones keep the
    slug reserved forever and return 410 without any page content.
    """

    cache_control = "no-store"
    if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
        return 404, {}, cache_control
    conn = _open_store()
    try:
        row = conn.execute(
            "SELECT page_id, slug, title, body, state FROM client_pages WHERE slug = ?",
            (slug,),
        ).fetchone()
    finally:
        conn.close()
    if row is None or row["state"] not in {"published", "tombstoned"}:
        return 404, {}, cache_control
    if row["state"] == "tombstoned":
        return 410, {}, cache_control
    try:
        rendered = account_pages.render_client_page({"title": row["title"], "body": row["body"]})
    except account_pages.PageValidationError:
        return 404, {}, cache_control
    return 200, {"page_id": row["page_id"], "slug": row["slug"], "html": rendered}, cache_control


def verify_client_page_publication(page_id: str, expected_state: str) -> dict[str, Any]:
    """Read back publication state without exposing account or page content."""

    if not _validate_ref(page_id) or expected_state not in {"published", "tombstoned"}:
        return _safe_result("failed", code="invalid_reference")
    conn = _open_store()
    try:
        row = conn.execute(
            "SELECT state, page_version FROM client_pages WHERE page_id = ?", (page_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return _safe_result("failed", code="not_found")
    if row["state"] != expected_state:
        return _safe_result("failed", code="state_mismatch", expected_state=expected_state, page_version=row["page_version"])
    return _safe_result(
        "verified_success",
        page_id=page_id,
        expected_state=expected_state,
        page_version=row["page_version"],
        public_status=200 if expected_state == "published" else 410,
    )


__all__ = [
    "LifecycleContext",
    "SAFE_STATES",
    "_connect",
    "_init_schema",
    "_safe_result",
    "prepare_client_page_create",
    "prepare_client_page_remove",
    "prepare_trusted_phone_add",
    "prepare_trusted_phone_remove",
    "bind_operation_confirmation",
    "commit_account_operation",
    "get_lifecycle_status",
    "cancel_account_operation",
    "get_public_page",
    "verify_client_page_publication",
]
