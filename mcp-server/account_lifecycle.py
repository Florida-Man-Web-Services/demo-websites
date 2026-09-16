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
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import account_pages
import customers
from account_verification import AuthContext, TrustedInputEvent, is_auth_context

SAFE_STATES = frozenset(
    {
        "denied",
        "verification_required",
        "awaiting_confirmation",
        "pending",
        "verified_success",
        "verified_noop",
        "failed",
        "pending_reconciliation",
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
        "phone_ref",
        "receipt_id",
        "new_auth_revision",
        "changed",
        "blocked",
    }
)
_SLUG_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_IDEMPOTENCY_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_OPAQUE_REF_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


LifecycleContext = AuthContext

_failure_injector = None
_failure_points: set[str] = set()
_blocked_accounts: set[str] = set()
_blocked_accounts_lock = threading.Lock()
_phone_apply_lock = threading.RLock()
FAILURE_POINT: str | None = None


def set_failure_injector(injector: Any | None) -> None:
    """Install a test-only callback invoked at named crash boundaries."""

    global _failure_injector
    _failure_injector = injector


def set_failure_point(point: str | None) -> None:
    """Install one test-only failure point, or clear it with ``None``."""

    global FAILURE_POINT
    _failure_points.clear()
    FAILURE_POINT = point
    if point:
        _failure_points.add(point)


def _fail_at(point: str) -> None:
    configured = os.getenv("ACCOUNT_LIFECYCLE_FAILURE_POINT", "").strip()
    if point in _failure_points or configured == point or FAILURE_POINT == point:
        raise RuntimeError(f"injected lifecycle failure at {point}")
    if _failure_injector is not None:
        _failure_injector(point)


def _block_account(account_id: str) -> None:
    with _blocked_accounts_lock:
        _blocked_accounts.add(account_id)


def _unblock_account(account_id: str) -> None:
    with _blocked_accounts_lock:
        _blocked_accounts.discard(account_id)


def _is_account_blocked(account_id: str) -> bool:
    with _blocked_accounts_lock:
        if account_id in _blocked_accounts:
            return True
    conn = _try_open_store()
    if conn is None:
        return False
    try:
        row = conn.execute(
            "SELECT 1 FROM operations WHERE account_id = ? AND state IN ('registry_pending', 'audit_pending', 'pending_reconciliation') LIMIT 1",
            (account_id,),
        ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


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
        if key == "phone_ref" and not _validate_phone_ref(value):
            continue
        if isinstance(value, (str, int, bool)):
            result[key] = value
    return result


def _denied(code: str) -> dict[str, Any]:
    return _safe_result("denied", code=code)


def _invalid_context(ctx: Any, action: str) -> bool:
    if not is_auth_context(ctx):
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
    if not ctx.has_capability(action):
        return True
    if _value(ctx, "account_status") not in ELIGIBLE_ACCOUNT_STATUSES:
        return True
    if _value(ctx, "owner_authenticated") is not True or _value(ctx, "proof_fresh") is not True:
        return True
    if _value(ctx, "auth_level") in {"cid_legacy", "soft", "cid_only"} or _value(ctx, "legacy_auth") is True:
        return True
    if _value(ctx, "forced_mode") is True:
        return True
    expires_at = _parse_time(_value(ctx, "expires_at"))
    return expires_at is None or expires_at <= _now()


def _guard(ctx: Any, action: str) -> dict[str, Any] | None:
    if not _enabled():
        return _denied("feature_disabled")
    if _invalid_context(ctx, action):
        return _denied("invalid_context")
    return None


def _authorize_registry_context(ctx: AuthContext, action: str) -> dict[str, Any]:
    """Apply the strict registry gate used by phone lifecycle operations."""

    return customers.authorize_account_lifecycle(
        account_id=_value(ctx, "account_id"),
        action=action,
        ctx=ctx,
        expected_auth_revision=_value(ctx, "auth_revision"),
    )


def _account_has_registry_identity(account_id: str) -> bool:
    with customers._lock:  # noqa: SLF001 - shared registry transaction boundary
        data = customers._read()  # noqa: SLF001
        return bool(customers._lifecycle_account_rows(data, account_id))  # noqa: SLF001


def authorize_account_lifecycle(
    *,
    account_id: str,
    action: str,
    ctx: AuthContext,
    expected_auth_revision: int | None = None,
) -> dict[str, Any]:
    """Expose the registry authorization boundary from the lifecycle service."""

    return customers.authorize_account_lifecycle(
        account_id=account_id,
        action=action,
        ctx=ctx,
        expected_auth_revision=expected_auth_revision,
    )


def resolve_lifecycle_account(caller_phone: str | None) -> dict[str, Any]:
    return customers.resolve_lifecycle_account(caller_phone)


def lifecycle_phone_refs(account_id: str, *, ctx: AuthContext) -> dict[str, Any]:
    return customers.lifecycle_phone_refs(account_id, ctx=ctx)


def _validate_e164(value: str | None) -> str | None:
    normalized = customers.normalize_phone(value)
    if not normalized or not re.fullmatch(r"\+[1-9][0-9]{9,14}", normalized):
        return None
    return normalized


def _validate_idempotency(key: str) -> bool:
    return isinstance(key, str) and bool(_IDEMPOTENCY_RE.fullmatch(key))


def _validate_ref(value: str) -> bool:
    return isinstance(value, str) and bool(_OPAQUE_REF_RE.fullmatch(value))


def _validate_phone_ref(value: str | None) -> bool:
    """Accept only the exact server-issued opaque phone-ref format."""

    return isinstance(value, str) and bool(re.fullmatch(r"phone_[0-9a-f]{32}", value))


def _phone_payload_digest(action: str, phone_ref: str | None = None) -> str | None:
    if action == "trusted_phone_add":
        return hashlib.sha256(b"trusted_phone_add").hexdigest()
    if action == "trusted_phone_remove" and _validate_phone_ref(phone_ref):
        return hashlib.sha256(f"trusted_phone_remove\0{phone_ref}".encode()).hexdigest()
    return None


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(16)}"


def _open_store() -> sqlite3.Connection:
    path = _db_path()
    _init_schema(path)
    return _connect(path)


def _try_open_store() -> sqlite3.Connection | None:
    """Open the store without allowing SQLite corruption to escape APIs."""

    try:
        return _open_store()
    except (sqlite3.Error, OSError):
        return None


def _rollback_safely(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error:
        pass


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
    if internal_state == "cancelled":
        public_state = "verified_noop"
    elif internal_state in {"registry_pending", "audit_pending", "pending_reconciliation"}:
        public_state = "pending_reconciliation"
    else:
        public_state = internal_state
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
    except (TypeError, ValueError):
        receipt = {}
    for key in (
        "page_id",
        "slug",
        "public_status",
        "page_version",
        "phone_ref",
        "receipt_id",
        "new_auth_revision",
        "changed",
        "code",
    ):
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
    ctx: AuthContext,
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
    if action in _PHONE_ACTIONS and _is_account_blocked(_value(ctx, "account_id")):
        return _safe_result("pending_reconciliation", blocked=True)
    conn = _try_open_store()
    if conn is None:
        return _safe_result("failed", code="storage_unavailable")
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
        _rollback_safely(conn)
        retry_conn = _try_open_store()
        if retry_conn is None:
            return _safe_result("failed", code="storage_unavailable")
        try:
            existing = _existing_operation(
                retry_conn, _value(ctx, "account_id"), idempotency_key, payload_digest
            )
        except sqlite3.Error:
            existing = None
        finally:
            retry_conn.close()
        return existing or _safe_result("failed", code="operation_conflict")
    except sqlite3.Error:
        _rollback_safely(conn)
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


def prepare_client_page_create(*, ctx: AuthContext, title: str, body: str, idempotency_key: str) -> dict[str, Any]:
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


def prepare_client_page_remove(*, ctx: AuthContext, page_id: str, idempotency_key: str) -> dict[str, Any]:
    """Prepare a page tombstone after checking idempotency and ownership."""

    guard = _guard(ctx, "client_page_remove")
    if guard:
        return guard
    if not _validate_ref(page_id):
        return _denied("not_found")
    if not _validate_idempotency(idempotency_key):
        return _denied("invalid_idempotency_key")
    digest = hashlib.sha256(f"client_page_remove\0{page_id}".encode()).hexdigest()
    conn = _try_open_store()
    if conn is None:
        return _safe_result("failed", code="storage_unavailable")
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = _existing_operation(
            conn, _value(ctx, "account_id"), idempotency_key, digest
        )
        if existing:
            conn.execute("COMMIT")
            return existing
        row = conn.execute(
            "SELECT page_id, account_id, state, page_version FROM client_pages WHERE page_id = ?",
            (page_id,),
        ).fetchone()
        if row is None or row["account_id"] != _value(ctx, "account_id") or row["state"] != "published":
            _rollback_safely(conn)
            return _denied("not_found")
        conn.execute("COMMIT")
    except sqlite3.Error:
        _rollback_safely(conn)
        return _safe_result("failed", code="storage_unavailable")
    finally:
        conn.close()
    return _create_operation(
        ctx=ctx,
        action="client_page_remove",
        idempotency_key=idempotency_key,
        payload_digest=digest,
        payload={"page_id": page_id},
        page_id=page_id,
        expected_page_version=row["page_version"],
    )


def prepare_trusted_phone_add(*, ctx: AuthContext, idempotency_key: str) -> dict[str, Any]:
    """Prepare a future verified destination-phone addition without raw input."""

    guard = _guard(ctx, "trusted_phone_add")
    if guard:
        return guard
    if _is_account_blocked(_value(ctx, "account_id")):
        return _safe_result("pending_reconciliation", blocked=True)
    auth = _authorize_registry_context(ctx, "trusted_phone_add")
    if not auth.get("ok"):
        return _denied(auth.get("code", "invalid_context"))
    digest = hashlib.sha256(b"trusted_phone_add").hexdigest()
    return _create_operation(
        ctx=ctx,
        action="trusted_phone_add",
        idempotency_key=idempotency_key,
        payload_digest=digest,
        payload={"action": "trusted_phone_add"},
    )


def prepare_trusted_phone_remove(*, ctx: AuthContext, phone_ref: str, idempotency_key: str) -> dict[str, Any]:
    """Prepare removal using only an opaque phone reference."""

    guard = _guard(ctx, "trusted_phone_remove")
    if guard:
        return guard
    if not _validate_phone_ref(phone_ref):
        return _denied("invalid_phone_ref")
    if _is_account_blocked(_value(ctx, "account_id")):
        return _safe_result("pending_reconciliation", blocked=True)
    auth = _authorize_registry_context(ctx, "trusted_phone_remove")
    if not auth.get("ok"):
        return _denied(auth.get("code", "invalid_context"))
    refs = customers.lifecycle_phone_refs(_value(ctx, "account_id"), ctx=ctx)
    if not refs.get("ok"):
        return _denied(refs.get("code", "account_unavailable"))
    if not any(item.get("phone_ref") == phone_ref for item in refs.get("phones", [])):
        return _denied("not_found")
    digest = hashlib.sha256(f"trusted_phone_remove\0{phone_ref}".encode()).hexdigest()
    return _create_operation(
        ctx=ctx,
        action="trusted_phone_remove",
        idempotency_key=idempotency_key,
        payload_digest=digest,
        payload={"phone_ref": phone_ref},
        phone_ref=phone_ref,
    )


def bind_operation_confirmation(*, ctx: AuthContext, operation_id: str, confirmation_digest: str) -> dict[str, Any]:
    """Bind a server-created keypad token digest before commit.

    This small boundary lets Task 3 create the single-use token without ever
    putting the token in a model-facing result or in the operation payload.
    """

    if not _enabled():
        return _denied("feature_disabled")
    if not is_auth_context(ctx) or not _validate_ref(operation_id):
        return _denied("invalid_context")
    if not isinstance(confirmation_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", confirmation_digest):
        return _denied("invalid_confirmation")
    conn = _try_open_store()
    if conn is None:
        return _safe_result("failed", code="storage_unavailable")
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM operations WHERE operation_id = ? AND account_id = ?",
            (operation_id, _value(ctx, "account_id")),
        ).fetchone()
        if row is None or _invalid_context(ctx, row["action"]):
            _rollback_safely(conn)
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
        _rollback_safely(conn)
        return _safe_result("failed", code="storage_unavailable")
    finally:
        conn.close()
    return _safe_result("awaiting_confirmation", operation_id=operation_id, confirmation_digest=confirmation_digest)


def commit_account_operation(*, ctx: AuthContext, operation_id: str, confirmation_token: str) -> dict[str, Any]:
    """Commit a confirmed page operation with all checks in one transaction."""

    if not _enabled():
        return _denied("feature_disabled")
    if not is_auth_context(ctx):
        return _denied("invalid_context")
    if not _validate_ref(operation_id) or not isinstance(confirmation_token, str):
        return _denied("invalid_confirmation")
    conn = _try_open_store()
    if conn is None:
        return _safe_result("failed", operation_id=operation_id, code="storage_unavailable")
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM operations WHERE operation_id = ? AND account_id = ?",
            (operation_id, _value(ctx, "account_id")),
        ).fetchone()
        if row is None:
            _rollback_safely(conn)
            return _denied("not_found")
        if row["state"] in FINAL_OPERATION_STATES:
            conn.execute("COMMIT")
            return _operation_result(row)
        if _invalid_context(ctx, row["action"]):
            _rollback_safely(conn)
            return _denied("invalid_context")
        expires_at = _parse_time(row["expires_at"])
        if expires_at is None or expires_at <= _now():
            expiry_receipt = {
                "operation_id": operation_id,
                "action": row["action"],
                "payload_digest": row["payload_digest"],
                "code": "expired",
            }
            conn.execute(
                "UPDATE operations SET state = 'failed', receipt_json = ?, updated_at = ? WHERE operation_id = ?",
                (json.dumps(expiry_receipt, sort_keys=True), _now_iso(), operation_id),
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
            expired_row = conn.execute(
                "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            return _operation_result(expired_row)
        if row["expected_auth_revision"] != _value(ctx, "auth_revision"):
            _rollback_safely(conn)
            return _denied("stale_auth_revision")
        confirmation_digest = hashlib.sha256(confirmation_token.encode("utf-8")).hexdigest()
        if not row["confirmation_digest"]:
            _rollback_safely(conn)
            return _safe_result("verification_required", operation_id=operation_id, code="confirmation_required")
        if not secrets.compare_digest(row["confirmation_digest"], confirmation_digest):
            _rollback_safely(conn)
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
                    _rollback_safely(conn)
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
                _rollback_safely(conn)
                return _safe_result("failed", operation_id=operation_id, code="page_not_found")
            if page["state"] == "tombstoned":
                receipt.update(page_id=page["page_id"], slug=page["slug"], public_status=410)
            elif page["state"] != "published" or page["page_version"] != row["expected_page_version"]:
                _rollback_safely(conn)
                return _safe_result("failed", operation_id=operation_id, code="page_changed")
            else:
                conn.execute(
                    "UPDATE client_pages SET state = 'tombstoned', deleted_at = ?, page_version = page_version + 1 WHERE page_id = ?",
                    (_now_iso(), row["page_id"]),
                )
                receipt.update(page_id=page["page_id"], slug=page["slug"], public_status=410, page_version=page["page_version"] + 1)
        else:
            _rollback_safely(conn)
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
        committed_row = conn.execute(
            "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
    except (sqlite3.Error, KeyError, json.JSONDecodeError):
        try:
            _rollback_safely(conn)
        except sqlite3.Error:
            pass
        return _safe_result("failed", operation_id=operation_id, code="storage_unavailable")
    finally:
        conn.close()

    return _operation_result(committed_row)


def _phone_operation_row(operation_id: str) -> sqlite3.Row | None:
    conn = _try_open_store()
    if conn is None:
        return None
    try:
        return conn.execute(
            "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
    finally:
        conn.close()


def _set_phone_operation_pending(
    operation_id: str,
    account_id: str,
    phone_e164: str,
    receipt_id: str,
) -> bool:
    """Durably record private mutation intent before touching the registry."""

    conn = _try_open_store()
    if conn is None:
        return False
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT payload_json, payload_digest, state FROM operations WHERE operation_id = ? AND account_id = ?",
            (operation_id, account_id),
        ).fetchone()
        if row is None:
            _rollback_safely(conn)
            return False
        payload = json.loads(row["payload_json"] or "{}")
        # This is private operation state, never copied to an audit row/result.
        payload["phone_e164"] = phone_e164
        payload["receipt_id"] = receipt_id
        conn.execute(
            """UPDATE operations
               SET payload_json = ?, receipt_json = ?, state = 'registry_pending', updated_at = ?
               WHERE operation_id = ? AND account_id = ?""",
            (
                json.dumps(payload, sort_keys=True, ensure_ascii=False),
                json.dumps({"receipt_id": receipt_id}, sort_keys=True),
                _now_iso(),
                operation_id,
                account_id,
            ),
        )
        _audit(
            conn,
            operation_id=operation_id,
            account_id=account_id,
            event_type="mutation_intent",
            opaque_ref=operation_id,
            payload_digest=row["payload_digest"],
            metadata={"action": "trusted_phone_mutation", "state": "registry_pending"},
        )
        conn.execute("COMMIT")
        return True
    except (sqlite3.Error, TypeError, ValueError):
        _rollback_safely(conn)
        return False
    finally:
        conn.close()


def _set_phone_state(operation_id: str, account_id: str, state: str) -> bool:
    conn = _try_open_store()
    if conn is None:
        return False
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE operations SET state = ?, updated_at = ? WHERE operation_id = ? AND account_id = ?",
            (state, _now_iso(), operation_id, account_id),
        )
        conn.execute("COMMIT")
        return True
    except sqlite3.Error:
        _rollback_safely(conn)
        return False
    finally:
        conn.close()


def _phone_receipt(
    *,
    operation_id: str,
    action: str,
    receipt_id: str,
    phone_ref: str,
    changed: bool,
    new_auth_revision: int,
    code: str | None = None,
) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "operation_id": operation_id,
        "action": action,
        "receipt_id": receipt_id,
        "phone_ref": phone_ref,
        "changed": changed,
        "new_auth_revision": new_auth_revision,
    }
    if code:
        receipt["code"] = code
    return receipt


def _finalize_phone_operation(
    *,
    operation_id: str,
    account_id: str,
    receipt: dict[str, Any],
    state: str,
    event_type: str,
) -> dict[str, Any]:
    """Finalize a phone receipt and audit event in one SQLite transaction."""

    conn = _try_open_store()
    if conn is None:
        _block_account(account_id)
        return _safe_result("pending_reconciliation", operation_id=operation_id, blocked=True)
    try:
        _fail_at("before_audit_finalization")
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM operations WHERE operation_id = ? AND account_id = ?",
            (operation_id, account_id),
        ).fetchone()
        if row is None:
            _rollback_safely(conn)
            _block_account(account_id)
            return _safe_result("pending_reconciliation", operation_id=operation_id, blocked=True)
        if row["state"] in FINAL_OPERATION_STATES:
            conn.execute("COMMIT")
            return _operation_result(row)
        conn.execute(
            "UPDATE operations SET state = ?, receipt_json = ?, updated_at = ? WHERE operation_id = ? AND account_id = ?",
            (state, json.dumps(receipt, sort_keys=True), _now_iso(), operation_id, account_id),
        )
        _audit(
            conn,
            operation_id=operation_id,
            account_id=account_id,
            event_type=event_type,
            opaque_ref=receipt["receipt_id"],
            payload_digest=row["payload_digest"],
            metadata={"action": receipt["action"], "state": state},
        )
        _fail_at("after_audit_finalization")
        conn.execute("COMMIT")
    except (sqlite3.Error, RuntimeError, TypeError, ValueError):
        _rollback_safely(conn)
        _block_account(account_id)
        return _safe_result("pending_reconciliation", operation_id=operation_id, blocked=True)
    finally:
        conn.close()
    if state not in FINAL_OPERATION_STATES:
        _block_account(account_id)
        return _safe_result("pending_reconciliation", operation_id=operation_id, blocked=True)
    _unblock_account(account_id)
    row = _phone_operation_row(operation_id)
    return _operation_result(row) if row is not None else _safe_result("pending_reconciliation", operation_id=operation_id)


def _registry_phone_effect(
    data: dict[str, dict[str, Any]],
    *,
    account_id: str,
    action: str,
    phone: str | None,
    phone_ref: str | None,
    expected_revision: int,
    reconciling: bool = False,
) -> dict[str, Any]:
    """Validate and, for valid operations, apply a registry phone mutation."""

    registry_check = customers.validate_lifecycle_registry(data)
    if not registry_check["ok"]:
        return {"ok": False, "code": registry_check["code"]}
    rows = customers._lifecycle_account_rows(data, account_id)  # noqa: SLF001
    if len(rows) != 1 or not customers.is_owner_write_status(rows[0][1].get("status")):
        return {"ok": False, "code": "account_unavailable"}
    map_key, row = rows[0]
    current_revision = row.get("auth_revision")
    if not isinstance(current_revision, int):
        return {"ok": False, "code": "account_unavailable"}
    primary = customers.normalize_phone(map_key) or customers.normalize_phone(row.get("phone"))
    if not primary:
        return {"ok": False, "code": "account_unavailable"}
    trusted_raw = row.get("trusted_phones") or []
    if isinstance(trusted_raw, str):
        trusted_raw = [trusted_raw]
    if not isinstance(trusted_raw, list):
        return {"ok": False, "code": "ambiguous_phone_membership"}
    trusted: list[str] = []
    for value in trusted_raw:
        normalized = _validate_e164(value)
        if not normalized or normalized in trusted:
            return {"ok": False, "code": "ambiguous_phone_membership"}
        trusted.append(normalized)
    memberships = customers._lifecycle_phone_memberships(data)  # noqa: SLF001
    by_phone: dict[str, list[tuple[str, dict[str, Any], bool]]] = {}
    for key, member_row, member_phone, is_primary in memberships:
        by_phone.setdefault(member_phone, []).append((key, member_row, is_primary))
    if action == "trusted_phone_add":
        if phone is None:
            return {"ok": False, "code": "invalid_phone"}
        target = phone
        target_memberships = by_phone.get(target, [])
        same_account = [m for m in target_memberships if m[1].get("account_id") == account_id]
        other_account = [m for m in target_memberships if m[1].get("account_id") != account_id]
        if other_account:
            return {"ok": False, "code": "phone_collision"}
        if len(same_account) > 1:
            return {"ok": False, "code": "ambiguous_phone_membership"}
        if same_account:
            if current_revision != expected_revision:
                return {"ok": False, "code": "stale_auth_revision"}
            return {
                "ok": True,
                "changed": False,
                "phone_ref": customers._lifecycle_phone_ref(account_id, target),  # noqa: SLF001
                "new_auth_revision": current_revision,
                "code": "already_trusted",
            }
        if current_revision != expected_revision:
            return {"ok": False, "code": "stale_auth_revision"}
        trusted.append(target)
        row["trusted_phones"] = trusted
        row["auth_revision"] = current_revision + 1
        row["updated_at"] = _now_iso()
        return {
            "ok": True,
            "changed": True,
            "phone_ref": customers._lifecycle_phone_ref(account_id, target),  # noqa: SLF001
            "new_auth_revision": current_revision + 1,
        }
    if action != "trusted_phone_remove" or not phone_ref:
        return {"ok": False, "code": "unsupported_phone_action"}
    if current_revision != expected_revision:
        return {"ok": False, "code": "stale_auth_revision"}
    matching = [
        (member_key, member_row, member_phone, is_primary)
        for member_key, member_row, member_phone, is_primary in memberships
        if member_row.get("account_id") == account_id
        and customers._lifecycle_phone_ref(account_id, member_phone) == phone_ref  # noqa: SLF001
    ]
    if len(matching) != 1:
        return {"ok": False, "code": "not_found" if not matching else "ambiguous_phone_membership"}
    _, _, target, is_primary = matching[0]
    if is_primary:
        return {"ok": False, "code": "primary_phone_protected"}
    verification_keys = (
        "current_verification_phone",
        "verification_phone",
        "verification_phone_e164",
        "active_verification_phone",
    )
    current_verification = next(
        (
            customers.normalize_phone(row.get(key))
            for key in verification_keys
            if customers.normalize_phone(row.get(key))
        ),
        None,
    )
    current_verification_ref = next(
        (
            row.get(key)
            for key in ("current_verification_phone_ref", "verification_phone_ref")
            if isinstance(row.get(key), str) and row.get(key)
        ),
        None,
    )
    if current_verification == target or current_verification_ref == phone_ref:
        return {"ok": False, "code": "verification_phone_protected"}
    if len(trusted) <= 1:
        return {"ok": False, "code": "last_trusted_phone_protected"}
    row["trusted_phones"] = [value for value in trusted if value != target]
    row["auth_revision"] = current_revision + 1
    row["updated_at"] = _now_iso()
    return {
        "ok": True,
        "changed": True,
        "phone_ref": phone_ref,
        "new_auth_revision": current_revision + 1,
    }


def _registry_phone_effect_matches(
    data: dict[str, dict[str, Any]],
    *,
    account_id: str,
    action: str,
    phone: str | None,
    phone_ref: str | None,
    expected_revision: int,
    changed: bool,
) -> bool:
    """Check a post-replacement registry snapshot without mutating it."""

    row = customers._lifecycle_account_rows(data, account_id)  # noqa: SLF001
    if len(row) != 1:
        return False
    _, account_row = row[0]
    revision = account_row.get("auth_revision")
    if not isinstance(revision, int):
        return False
    trusted_raw = account_row.get("trusted_phones") or []
    if isinstance(trusted_raw, str):
        trusted_raw = [trusted_raw]
    trusted = [_validate_e164(value) for value in trusted_raw] if isinstance(trusted_raw, list) else []
    if any(value is None for value in trusted):
        return False
    memberships = customers._lifecycle_phone_memberships(data)  # noqa: SLF001
    if action == "trusted_phone_add" and phone:
        count = sum(
            1 for _, member_row, member_phone, _ in memberships
            if member_row.get("account_id") == account_id and member_phone == phone
        )
        return count == 1 and (revision == expected_revision + 1 if changed else revision == expected_revision)
    if action == "trusted_phone_remove" and phone_ref:
        count = sum(
            1
            for _, member_row, member_phone, _ in memberships
            if member_row.get("account_id") == account_id
            and customers._lifecycle_phone_ref(account_id, member_phone) == phone_ref  # noqa: SLF001
        )
        return count == 0 and revision == expected_revision + 1
    return False


def apply_trusted_phone_operation(
    *,
    operation_id: str,
    account_id: str,
    action: str,
    phone_e164: str,
    expected_auth_revision: int,
    _reconciling: bool = False,
) -> dict[str, Any]:
    """Serialize phone applies so one operation has one durable receipt."""

    with _phone_apply_lock:
        return _apply_trusted_phone_operation(
            operation_id=operation_id,
            account_id=account_id,
            action=action,
            phone_e164=phone_e164,
            expected_auth_revision=expected_auth_revision,
            _reconciling=_reconciling,
        )


def _apply_trusted_phone_operation(
    *,
    operation_id: str,
    account_id: str,
    action: str,
    phone_e164: str,
    expected_auth_revision: int,
    _reconciling: bool = False,
) -> dict[str, Any]:
    """Apply a prepared phone operation under the process-wide registry lock."""

    if not _enabled():
        return _denied("feature_disabled")
    if not isinstance(action, str) or action not in _PHONE_ACTIONS or not _validate_ref(operation_id):
        return _denied("invalid_context")
    if not isinstance(account_id, str) or not account_id.strip() or type(expected_auth_revision) is not int:
        return _denied("invalid_context")
    row = _phone_operation_row(operation_id)
    if row is None or row["account_id"] != account_id or row["action"] != action:
        return _denied("not_found")
    if row["expected_auth_revision"] != expected_auth_revision:
        return _denied("stale_auth_revision")
    try:
        intent = json.loads(row["payload_json"] or "{}")
    except (TypeError, ValueError):
        return _denied("operation_integrity_error")
    prepared_ref = intent.get("phone_ref") if action == "trusted_phone_remove" else None
    expected_digest = _phone_payload_digest(action, prepared_ref)
    if row["payload_digest"] != expected_digest:
        return _denied("operation_integrity_error")
    if action == "trusted_phone_add" and intent.get("action") != "trusted_phone_add":
        return _denied("operation_integrity_error")
    if action == "trusted_phone_add" and row["phone_ref"] is not None:
        return _denied("operation_integrity_error")
    if action == "trusted_phone_remove" and (
        row["phone_ref"] != prepared_ref or not _validate_phone_ref(prepared_ref)
    ):
        return _denied("operation_integrity_error")
    if row["state"] in FINAL_OPERATION_STATES:
        return _operation_result(row)
    if not _reconciling and _is_account_blocked(account_id):
        return _safe_result("pending_reconciliation", operation_id=operation_id, blocked=True)
    phone_ref = intent.get("phone_ref") if action == "trusted_phone_remove" else None
    target = _validate_e164(phone_e164) if action == "trusted_phone_add" else None
    if action == "trusted_phone_add" and target is None:
        return _denied("invalid_phone")
    receipt_id = intent.get("receipt_id") or _new_id("receipt")
    if not _set_phone_operation_pending(operation_id, account_id, target or "", receipt_id):
        _block_account(account_id)
        return _safe_result("pending_reconciliation", operation_id=operation_id, blocked=True)
    try:
        _fail_at("before_registry_replacement")
        with customers._lock:  # noqa: SLF001 - registry-wide mutation lock
            data = customers._read()  # noqa: SLF001
            customers._migrate_lifecycle_identity(data)  # noqa: SLF001
            effect = _registry_phone_effect(
                data,
                account_id=account_id,
                action=action,
                phone=target,
                phone_ref=phone_ref,
                expected_revision=expected_auth_revision,
                reconciling=_reconciling,
            )
            if not effect.get("ok"):
                if _reconciling:
                    _block_account(account_id)
                    return _safe_result("pending_reconciliation", operation_id=operation_id, blocked=True)
                receipt = _phone_receipt(
                    operation_id=operation_id,
                    action=action,
                    receipt_id=receipt_id,
                    phone_ref=phone_ref or "",
                    changed=False,
                    new_auth_revision=expected_auth_revision,
                    code=effect.get("code", "mutation_denied"),
                )
                return _finalize_phone_operation(
                    operation_id=operation_id,
                    account_id=account_id,
                    receipt=receipt,
                    state="failed",
                    event_type="mutation_denied",
                )
            if effect["changed"]:
                customers._write(data)  # noqa: SLF001
            readback = customers._read()  # noqa: SLF001
            verified = _registry_phone_effect_matches(
                readback,
                account_id=account_id,
                action=action,
                phone=target,
                phone_ref=phone_ref,
                expected_revision=expected_auth_revision,
                changed=effect["changed"],
            )
            if not verified:
                _block_account(account_id)
                return _safe_result("pending_reconciliation", operation_id=operation_id, blocked=True)
            if not _set_phone_state(operation_id, account_id, "audit_pending"):
                _block_account(account_id)
                return _safe_result("pending_reconciliation", operation_id=operation_id, blocked=True)
        _fail_at("after_registry_replacement")
    except (OSError, RuntimeError, sqlite3.Error, TypeError, ValueError):
        _block_account(account_id)
        return _safe_result("pending_reconciliation", operation_id=operation_id, blocked=True)
    receipt = _phone_receipt(
        operation_id=operation_id,
        action=action,
        receipt_id=receipt_id,
        phone_ref=effect["phone_ref"],
        changed=effect["changed"],
        new_auth_revision=effect["new_auth_revision"],
        code="already_trusted" if not effect["changed"] else None,
    )
    return _finalize_phone_operation(
        operation_id=operation_id,
        account_id=account_id,
        receipt=receipt,
        state="verified_noop" if not effect["changed"] else "verified_success",
        event_type="mutation_committed",
    )


def _pending_operation_rows(account_id: str | None = None) -> list[sqlite3.Row] | None:
    conn = _try_open_store()
    if conn is None:
        return None
    try:
        if account_id:
            return conn.execute(
                "SELECT * FROM operations WHERE account_id = ? AND state IN ('registry_pending', 'audit_pending', 'pending_reconciliation') ORDER BY created_at",
                (account_id,),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM operations WHERE state IN ('registry_pending', 'audit_pending', 'pending_reconciliation') ORDER BY created_at"
        ).fetchall()
    finally:
        conn.close()


def _reconcile_exact_pending(row: sqlite3.Row, intent: dict[str, Any]) -> dict[str, Any] | None:
    """Return a receipt only when pending state has an exact read-back."""

    action = row["action"]
    account_id = row["account_id"]
    phone = _validate_e164(intent.get("phone_e164")) if action == "trusted_phone_add" else None
    phone_ref = intent.get("phone_ref") if action == "trusted_phone_remove" else None
    if action == "trusted_phone_add" and phone is None:
        return None
    with customers._lock:  # noqa: SLF001
        data = customers._read()  # noqa: SLF001
        if not customers.validate_lifecycle_registry(data).get("ok"):
            return None
        rows = customers._lifecycle_account_rows(data, account_id)  # noqa: SLF001
        if len(rows) != 1 or not customers.is_owner_write_status(rows[0][1].get("status")):
            return None
        revision = rows[0][1].get("auth_revision")
        memberships = customers._lifecycle_phone_memberships(data)  # noqa: SLF001
        if action == "trusted_phone_add":
            count = sum(
                1 for _, member_row, member_phone, _ in memberships
                if member_row.get("account_id") == account_id and member_phone == phone
            )
            if count != 1 or revision not in {row["expected_auth_revision"], row["expected_auth_revision"] + 1}:
                return None
            changed = revision == row["expected_auth_revision"] + 1
            ref = customers._lifecycle_phone_ref(account_id, phone)  # noqa: SLF001
        elif action == "trusted_phone_remove" and phone_ref:
            count = sum(
                1 for _, member_row, member_phone, _ in memberships
                if member_row.get("account_id") == account_id
                and customers._lifecycle_phone_ref(account_id, member_phone) == phone_ref  # noqa: SLF001
            )
            if count != 0 or revision != row["expected_auth_revision"] + 1:
                return None
            changed = True
            ref = phone_ref
        else:
            return None
    receipt_id = intent.get("receipt_id") or _new_id("receipt")
    receipt = _phone_receipt(
        operation_id=row["operation_id"],
        action=action,
        receipt_id=receipt_id,
        phone_ref=ref,
        changed=changed,
        new_auth_revision=revision,
        code="already_trusted" if not changed else None,
    )
    return _finalize_phone_operation(
        operation_id=row["operation_id"],
        account_id=account_id,
        receipt=receipt,
        state="verified_noop" if not changed else "verified_success",
        event_type="mutation_reconciled",
    )


def _pending_recovery_is_well_formed(row: sqlite3.Row, intent: Any) -> bool:
    """Reject malformed/unavailable recovery inputs without replaying them."""

    if not isinstance(intent, dict) or row["action"] not in _PHONE_ACTIONS:
        return False
    action = row["action"]
    if action == "trusted_phone_add":
        if _validate_e164(intent.get("phone_e164")) is None:
            return False
        if intent.get("action") != action:
            return False
        expected_digest = _phone_payload_digest(action)
    else:
        phone_ref = intent.get("phone_ref")
        if not _validate_phone_ref(phone_ref) or row["phone_ref"] != phone_ref:
            return False
        expected_digest = _phone_payload_digest(action, phone_ref)
    if row["payload_digest"] != expected_digest:
        return False
    with customers._lock:  # noqa: SLF001
        data = customers._read()  # noqa: SLF001
        if not customers.validate_lifecycle_registry(data).get("ok"):
            return False
        account_rows = customers._lifecycle_account_rows(data, row["account_id"])  # noqa: SLF001
        if len(account_rows) != 1 or not customers.is_owner_write_status(account_rows[0][1].get("status")):
            return False
        revision = account_rows[0][1].get("auth_revision")
        return isinstance(revision, int) and not isinstance(revision, bool)


def reconcile_pending_operations(*, account_id: str | None = None, operation_id: str | None = None) -> dict[str, Any]:
    """Serialize reconciliation with applies for stable operation receipts."""

    with _phone_apply_lock:
        return _reconcile_pending_operations(account_id=account_id, operation_id=operation_id)


def _reconcile_pending_operations(*, account_id: str | None = None, operation_id: str | None = None) -> dict[str, Any]:
    """Resolve pending phone operations only after exact registry read-back."""

    rows = _pending_operation_rows(account_id)
    if rows is None:
        return {"ok": False, "state": "pending_reconciliation", "code": "reconciliation_unavailable"}
    if operation_id:
        rows = [row for row in rows if row["operation_id"] == operation_id]
        if not rows:
            return {"ok": False, "state": "pending_reconciliation", "code": "reconciliation_unavailable"}
    results: list[dict[str, Any]] = []
    for row in rows:
        try:
            intent = json.loads(row["payload_json"] or "{}")
            if not _pending_recovery_is_well_formed(row, intent):
                _block_account(row["account_id"])
                result = _safe_result("pending_reconciliation", operation_id=row["operation_id"], blocked=True)
            else:
                result = _reconcile_exact_pending(row, intent)
                if result is None:
                    result = apply_trusted_phone_operation(
                        operation_id=row["operation_id"],
                        account_id=row["account_id"],
                        action=row["action"],
                        phone_e164=intent.get("phone_e164", ""),
                        expected_auth_revision=row["expected_auth_revision"],
                        _reconciling=True,
                    )
        except (TypeError, ValueError, KeyError):
            _block_account(row["account_id"])
            result = _safe_result("pending_reconciliation", operation_id=row["operation_id"], blocked=True)
        if result.get("state") not in {"verified_success", "verified_noop", "pending_reconciliation"}:
            _block_account(row["account_id"])
            result = _safe_result("pending_reconciliation", operation_id=row["operation_id"], blocked=True)
        results.append(result)
    unresolved = [result for result in results if result.get("state") == "pending_reconciliation"]
    if unresolved:
        return {"ok": False, "state": "pending_reconciliation", "count": len(results), "operations": results}
    return {
        "ok": True,
        "state": "verified_success" if results else "verified_noop",
        "count": len(results),
        "operations": results,
    }


def get_lifecycle_status(*, ctx: AuthContext, operation_id: str | None = None) -> dict[str, Any]:
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
    conn = _try_open_store()
    if conn is None:
        return _safe_result("failed", code="storage_unavailable")
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
    except sqlite3.Error:
        return _safe_result("failed", code="storage_unavailable")
    finally:
        conn.close()
    return _operation_result(row) if row else _denied("not_found")


def cancel_account_operation(*, ctx: AuthContext, operation_id: str) -> dict[str, Any]:
    """Cancel an uncommitted operation; cancellation is idempotent."""

    if not _enabled():
        return _denied("feature_disabled")
    if not is_auth_context(ctx):
        return _denied("invalid_context")
    if not _validate_ref(operation_id):
        return _denied("not_found")
    conn = _try_open_store()
    if conn is None:
        return _safe_result("failed", code="storage_unavailable")
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM operations WHERE operation_id = ? AND account_id = ?",
            (operation_id, _value(ctx, "account_id")),
        ).fetchone()
        if row is None:
            _rollback_safely(conn)
            return _denied("not_found")
        if row["state"] in FINAL_OPERATION_STATES:
            conn.execute("COMMIT")
            return _operation_result(row)
        if _invalid_context(ctx, row["action"]):
            _rollback_safely(conn)
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
        _rollback_safely(conn)
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
    conn = _try_open_store()
    if conn is None:
        return 404, {}, cache_control
    try:
        row = conn.execute(
            "SELECT page_id, slug, title, body, state FROM client_pages WHERE slug = ?",
            (slug,),
        ).fetchone()
    except sqlite3.Error:
        return 404, {}, cache_control
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
    conn = _try_open_store()
    if conn is None:
        return _safe_result("failed", code="storage_unavailable")
    try:
        row = conn.execute(
            "SELECT state, page_version FROM client_pages WHERE page_id = ?", (page_id,)
        ).fetchone()
    except sqlite3.Error:
        return _safe_result("failed", code="storage_unavailable")
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
    "AuthContext",
    "TrustedInputEvent",
    "LifecycleContext",
    "SAFE_STATES",
    "_connect",
    "_init_schema",
    "_safe_result",
    "resolve_lifecycle_account",
    "authorize_account_lifecycle",
    "lifecycle_phone_refs",
    "prepare_client_page_create",
    "prepare_client_page_remove",
    "prepare_trusted_phone_add",
    "prepare_trusted_phone_remove",
    "apply_trusted_phone_operation",
    "reconcile_pending_operations",
    "set_failure_injector",
    "set_failure_point",
    "bind_operation_confirmation",
    "commit_account_operation",
    "get_lifecycle_status",
    "cancel_account_operation",
    "get_public_page",
    "verify_client_page_publication",
]
