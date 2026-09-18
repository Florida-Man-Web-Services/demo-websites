"""Customer lifecycle registry for FMWS voice + AI 411 onboarding.

Statuses drive per-call AGENT_MODE routing when AGENT_MODE=auto (recommended):

  unknown / no row          → ai411          (default public line)
  resume_waitlist           → ai411          (resume_web waitlist; not website onboarding)
  onboarding | callback_queued → onboarding  (requirements interview)
  requirements_ready | demo_ready | sales_ready → sales
  paid | active_owner       → owner_updates

Also holds website requirements (interview output), demo URL, Stripe payment
link, and builder-brief path for the coding agent.

Storage: JSON map phone_e164 → Customer, path CUSTOMERS_PATH
(default /data/customers.json or repo data/customers.json).
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from account_verification import is_auth_context

_REPO = Path(__file__).resolve().parent.parent
_DEFAULT = (
    Path("/data/customers.json")
    if Path("/data").is_dir()
    else _REPO / "data" / "customers.json"
)
CUSTOMERS_PATH = Path(os.getenv("CUSTOMERS_PATH", str(_DEFAULT)))

_lock = threading.Lock()

# Lifecycle statuses (ordered roughly by funnel).
STATUSES = [
    "prospect",            # web signup, not yet called
    "callback_queued",     # waiting for onboarding outbound/inbound
    "resume_waitlist",     # resume_web signup; stay on ai411, never onboard
    "onboarding",          # mid-interview
    "requirements_ready",  # interview done; ready for builder
    "building",            # coding agent working
    "demo_ready",          # site live; ready for sales call
    "sales_ready",         # alias of demo_ready for dialer
    "paid",                # Stripe checkout completed
    "active_owner",        # paying customer — owner_updates mode
    "churned",
    "do_not_call",
]

# Modes the router may return.
MODE_AI411 = "ai411"
MODE_ONBOARDING = "onboarding"
MODE_SALES = "sales"
MODE_OWNER = "owner_updates"

# Statuses allowed to mutate sites (owner_updates write path).
OWNER_WRITE_STATUSES = frozenset({"paid", "active_owner"})
_E164_RE = re.compile(r"\A\+[1-9][0-9]{9,14}\Z")

LIFECYCLE_ACTIONS = frozenset(
    {
        "client_page_create",
        "client_page_remove",
        "trusted_phone_add",
        "trusted_phone_remove",
        "lifecycle_status",
        "get_lifecycle_status",
        "lifecycle_phone_refs",
    }
)
_FORBIDDEN_LIFECYCLE_AUTH_LEVELS = frozenset({"cid_legacy", "soft", "cid_only"})

# OWNER_CR_AUTH: soft (default) | strict | off
# soft  — paid slug owners are protected; unknown/unclaimed slugs stay legacy-open
# strict — every write needs a paid/active_owner trusted phone for that slug
# off   — no registry checks (tests / emergency)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_phone(phone: str | None) -> str | None:
    if phone is None:
        return None
    raw = str(phone).strip()
    if not raw:
        return None
    if raw.startswith("+"):
        digits = re.sub(r"\D", "", raw[1:])
        if len(digits) < 10:
            return None
        return "+" + digits
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 10:
        return None
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return "+" + digits


def is_valid_e164(phone: str | None) -> bool:
    """Return whether a value is already a valid canonical E.164 number."""

    return isinstance(phone, str) and bool(_E164_RE.fullmatch(phone))


def _path() -> Path:
    return Path(os.getenv("CUSTOMERS_PATH", str(CUSTOMERS_PATH)))


def _read() -> dict[str, dict[str, Any]]:
    path = _path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _migrate_lifecycle_identity(data: dict[str, dict[str, Any]]) -> bool:
    """Add lifecycle identity fields without changing registry map keys.

    Only eligible rows receive new identity fields.  Existing account IDs and
    revisions are preserved, and rows are never merged even if their contact
    information happens to collide.
    """

    changed = False
    for row in data.values():
        if not isinstance(row, dict) or not is_owner_write_status(row.get("status")):
            continue
        account_id = row.get("account_id")
        if not isinstance(account_id, str) or not account_id.strip():
            row["account_id"] = f"acct_{uuid.uuid4().hex}"
            changed = True
        revision = row.get("auth_revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            row["auth_revision"] = 0
            changed = True
    return changed


def _read_with_lifecycle_migration() -> dict[str, dict[str, Any]]:
    """Read the registry and persist eligible identity defaults atomically."""

    data = _read()
    if _migrate_lifecycle_identity(data):
        _write(data)
    return data


def migrate_lifecycle_registry() -> dict[str, Any]:
    """Run the idempotent lifecycle identity migration explicitly."""

    with _lock:
        data = _read()
        changed = _migrate_lifecycle_identity(data)
        if changed:
            _write(data)
    eligible = sum(
        1 for row in data.values()
        if isinstance(row, dict) and is_owner_write_status(row.get("status"))
    )
    return {"ok": True, "migrated": changed, "eligible_count": eligible}


def _write(data: dict[str, dict[str, Any]]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def get(phone: str) -> dict[str, Any] | None:
    key = normalize_phone(phone)
    if not key:
        return None
    with _lock:
        row = _read_with_lifecycle_migration().get(key)
        return dict(row) if row else None


def trusted_phones_for(customer: dict[str, Any] | None) -> list[str]:
    """Primary phone + trusted_phones + delegate phones (normalized, unique)."""
    if not customer:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def _add(raw: Any) -> None:
        key = normalize_phone(str(raw) if raw is not None else None)
        if key and key not in seen:
            seen.add(key)
            out.append(key)

    _add(customer.get("phone"))
    trusted = customer.get("trusted_phones") or []
    if isinstance(trusted, str):
        trusted = [trusted]
    if isinstance(trusted, list):
        for p in trusted:
            _add(p)
    delegates = customer.get("delegates") or []
    if isinstance(delegates, list):
        for d in delegates:
            if isinstance(d, dict):
                _add(d.get("phone"))
            else:
                _add(d)
    return out


def phone_is_trusted(customer: dict[str, Any] | None, phone: str | None) -> bool:
    key = normalize_phone(phone)
    if not key:
        return False
    return key in set(trusted_phones_for(customer))


def is_owner_write_status(status: str | None) -> bool:
    return (status or "").strip() in OWNER_WRITE_STATUSES


def find_customers_for_phone(phone: str | None) -> list[dict[str, Any]]:
    """All registry rows where phone is primary, trusted, or a delegate line."""
    key = normalize_phone(phone)
    if not key:
        return []
    with _lock:
        rows = list(_read_with_lifecycle_migration().values())
    return [dict(r) for r in rows if phone_is_trusted(r, key)]


def _lifecycle_phone_memberships(
    data: dict[str, dict[str, Any]],
) -> list[tuple[str, dict[str, Any], str, bool]]:
    """Return primary/trusted memberships as (map key, row, phone, primary)."""

    memberships: list[tuple[str, dict[str, Any], str, bool]] = []
    for map_key, row in data.items():
        if not isinstance(row, dict):
            continue
        primary = normalize_phone(map_key) or normalize_phone(row.get("phone"))
        if primary:
            memberships.append((map_key, row, primary, True))
        trusted = row.get("trusted_phones") or []
        if isinstance(trusted, str):
            trusted = [trusted]
        if isinstance(trusted, list):
            for value in trusted:
                normalized = normalize_phone(value)
                if normalized:
                    memberships.append((map_key, row, normalized, False))
    return memberships


def _lifecycle_account_rows(
    data: dict[str, dict[str, Any]], account_id: str,
) -> list[tuple[str, dict[str, Any]]]:
    return [
        (key, row)
        for key, row in data.items()
        if isinstance(row, dict) and row.get("account_id") == account_id
    ]


def validate_lifecycle_registry(data: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Validate every phone membership before any lifecycle decision.

    Lifecycle authorization is fail-closed: an unrelated malformed or
    duplicate membership is enough to stop the requested account operation.
    """

    if not isinstance(data, dict):
        return {"ok": False, "code": "account_unavailable"}
    memberships: list[tuple[str, dict[str, Any], str, bool]] = []
    account_ids: set[str] = set()
    for map_key, row in data.items():
        if not isinstance(row, dict):
            return {"ok": False, "code": "account_unavailable"}
        if is_owner_write_status(row.get("status")):
            account_id = row.get("account_id")
            if not isinstance(account_id, str) or not account_id.strip() or account_id in account_ids:
                return {"ok": False, "code": "account_unavailable"}
            account_ids.add(account_id)
        primary = normalize_phone(map_key)
        row_phone = normalize_phone(row.get("phone"))
        if primary is None and row_phone is None:
            return {"ok": False, "code": "ambiguous_phone_membership"}
        if primary is not None and not is_valid_e164(primary):
            return {"ok": False, "code": "ambiguous_phone_membership"}
        if row_phone is not None and not is_valid_e164(row_phone):
            return {"ok": False, "code": "ambiguous_phone_membership"}
        if primary is not None and row_phone is not None and primary != row_phone:
            return {"ok": False, "code": "ambiguous_phone_membership"}
        primary = primary or row_phone
        assert primary is not None
        memberships.append((map_key, row, primary, True))
        trusted_raw = row.get("trusted_phones", [])
        if trusted_raw is None:
            trusted_raw = []
        if not isinstance(trusted_raw, list):
            return {"ok": False, "code": "ambiguous_phone_membership"}
        trusted_seen: set[str] = set()
        for value in trusted_raw:
            normalized = normalize_phone(value) if isinstance(value, str) else None
            if not is_valid_e164(normalized) or normalized in trusted_seen:
                return {"ok": False, "code": "ambiguous_phone_membership"}
            assert normalized is not None
            trusted_seen.add(normalized)
            memberships.append((map_key, row, normalized, False))
    counts: dict[str, int] = {}
    for _, _, phone, _ in memberships:
        counts[phone] = counts.get(phone, 0) + 1
    if any(count > 1 for count in counts.values()):
        return {"ok": False, "code": "ambiguous_phone_membership"}
    return {"ok": True}


def _lifecycle_phone_ref(account_id: str, phone: str) -> str:
    """Derive a stable opaque reference; never embed the phone in the ref."""

    digest = hashlib.sha256(f"{account_id}\0{phone}".encode("utf-8")).hexdigest()
    return f"phone_{digest[:32]}"


def _mask_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    return f"••••{digits[-4:]}" if len(digits) >= 4 else "••••"


def resolve_lifecycle_account(caller_phone: str | None) -> dict[str, Any]:
    """Resolve one and only one eligible account using a caller hint.

    The caller number is a lookup hint, not authorization.  None and
    ambiguous matches deliberately return the same generic denial.
    """

    key = normalize_phone(caller_phone)
    if not key:
        return {"ok": False, "code": "account_unavailable", "error": "account unavailable"}
    with _lock:
        data = _read_with_lifecycle_migration()
        registry_check = validate_lifecycle_registry(data)
        if not registry_check["ok"]:
            return {"ok": False, "code": "account_unavailable", "error": "account unavailable"}
        matched_rows: dict[int, dict[str, Any]] = {}
        for _, row, member_phone, _ in _lifecycle_phone_memberships(data):
            if is_owner_write_status(row.get("status")) and member_phone == key:
                matched_rows[id(row)] = row
        matches = list(matched_rows.values())
    if len(matches) != 1:
        return {"ok": False, "code": "account_unavailable", "error": "account unavailable"}
    row = matches[0]
    return {
        "ok": True,
        "account_id": row["account_id"],
        "auth_revision": row["auth_revision"],
        "account_status": row.get("status"),
    }


def authorize_account_lifecycle(
    *,
    account_id: str,
    action: str,
    ctx: Any,
    expected_auth_revision: int | None = None,
) -> dict[str, Any]:
    """Authorize lifecycle access against the current registry row."""

    if not is_auth_context(ctx):
        return {"ok": False, "code": "invalid_context", "error": "lifecycle access denied"}
    if action not in LIFECYCLE_ACTIONS or not ctx.has_capability(action):
        return {"ok": False, "code": "invalid_context", "error": "lifecycle access denied"}
    if not isinstance(account_id, str) or not account_id.strip() or ctx.account_id != account_id:
        return {"ok": False, "code": "account_unavailable", "error": "account unavailable"}
    if ctx.owner_authenticated is not True or ctx.proof_fresh is not True:
        return {"ok": False, "code": "verification_required", "error": "lifecycle access denied"}
    if ctx.auth_level in _FORBIDDEN_LIFECYCLE_AUTH_LEVELS or ctx.legacy_auth:
        return {"ok": False, "code": "invalid_context", "error": "lifecycle access denied"}
    if getattr(ctx, "forced_mode", False):
        return {"ok": False, "code": "invalid_context", "error": "lifecycle access denied"}
    with _lock:
        data = _read_with_lifecycle_migration()
        registry_check = validate_lifecycle_registry(data)
        if not registry_check["ok"]:
            return {"ok": False, "code": registry_check["code"], "error": "lifecycle access denied"}
        rows = _lifecycle_account_rows(data, account_id)
        if len(rows) != 1 or not is_owner_write_status(rows[0][1].get("status")):
            return {"ok": False, "code": "account_unavailable", "error": "account unavailable"}
        row = rows[0][1]
        if ctx.account_status != row.get("status"):
            return {"ok": False, "code": "account_unavailable", "error": "account unavailable"}
        current_revision = row.get("auth_revision")
        if not isinstance(current_revision, int) or current_revision < 0:
            return {"ok": False, "code": "account_unavailable", "error": "account unavailable"}
    if expected_auth_revision is not None and current_revision != expected_auth_revision:
        return {"ok": False, "code": "stale_auth_revision", "error": "authorization is stale"}
    if current_revision != ctx.auth_revision:
        return {"ok": False, "code": "stale_auth_revision", "error": "authorization is stale"}
    return {
        "ok": True,
        "account_id": account_id,
        "auth_revision": current_revision,
        "account_status": row.get("status"),
    }


def lifecycle_phone_refs(account_id: str, *, ctx: Any) -> dict[str, Any]:
    """List only stable opaque phone refs and masked labels for one account."""

    action = getattr(ctx, "action", None)
    if action not in {"trusted_phone_add", "trusted_phone_remove", "lifecycle_phone_refs"}:
        return {"ok": False, "code": "invalid_context", "error": "lifecycle access denied"}
    auth = authorize_account_lifecycle(account_id=account_id, action=action, ctx=ctx)
    if not auth.get("ok"):
        return auth
    with _lock:
        data = _read_with_lifecycle_migration()
        registry_check = validate_lifecycle_registry(data)
        if not registry_check["ok"]:
            return {"ok": False, "code": registry_check["code"], "error": "lifecycle access denied"}
        rows = _lifecycle_account_rows(data, account_id)
        if len(rows) != 1:
            return {"ok": False, "code": "account_unavailable", "error": "account unavailable"}
        map_key, row = rows[0]
        all_memberships = _lifecycle_phone_memberships(data)
        counts: dict[str, int] = {}
        for _, _, phone, _ in all_memberships:
            counts[phone] = counts.get(phone, 0) + 1
        if any(count > 1 for count in counts.values()):
            return {"ok": False, "code": "ambiguous_phone_membership", "error": "lifecycle access denied"}
        primary = normalize_phone(map_key) or normalize_phone(row.get("phone"))
        if not primary:
            return {"ok": False, "code": "account_unavailable", "error": "account unavailable"}
        refs = [{
            "phone_ref": _lifecycle_phone_ref(account_id, primary),
            "label": f"primary {_mask_phone(primary)}",
            "is_primary": True,
        }]
        trusted = row.get("trusted_phones") or []
        if isinstance(trusted, str):
            trusted = [trusted]
        for value in trusted if isinstance(trusted, list) else []:
            phone = normalize_phone(value)
            if phone:
                refs.append({
                    "phone_ref": _lifecycle_phone_ref(account_id, phone),
                    "label": f"trusted {_mask_phone(phone)}",
                    "is_primary": False,
                })
    return {"ok": True, "account_id": account_id, "auth_revision": auth["auth_revision"], "phones": refs}


def slugs_owned(customer: dict[str, Any] | None) -> list[str]:
    """Primary slug plus owned_slugs (unique, stripped, order-preserving)."""
    if not customer:
        return []
    out: list[str] = []
    primary = (customer.get("slug") or "").strip()
    if primary:
        out.append(primary)
    extra = customer.get("owned_slugs") or []
    if isinstance(extra, str):
        extra = [extra]
    for item in extra:
        s = (item or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def owners_of_slug(slug: str | None) -> list[dict[str, Any]]:
    """Paid/active_owner customers whose primary slug or owned_slugs match."""
    s = (slug or "").strip()
    if not s:
        return []
    with _lock:
        rows = list(_read().values())
    out: list[dict[str, Any]] = []
    for r in rows:
        if not is_owner_write_status(r.get("status")):
            continue
        if s in slugs_owned(r):
            out.append(dict(r))
    return out


def cr_auth_mode() -> str:
    raw = (os.getenv("OWNER_CR_AUTH") or "soft").strip().lower()
    if raw in ("off", "0", "false", "no"):
        return "off"
    if raw in ("strict", "require", "on", "1", "true"):
        return "strict"
    return "soft"


def authorize_owner_write(
    caller_phone: str | None,
    business_slug: str | None,
    *,
    action: str = "write",
) -> dict[str, Any]:
    """Server-side F1 gate for owner ChangeRequest mutates (Phase 0).

    Returns {ok: True, auth_level, customer?} or {ok: False, error, code}.
    Never raises.
    """
    mode = cr_auth_mode()
    if mode == "off":
        return {
            "ok": True,
            "auth_level": "cid_legacy",
            "auth_mode": mode,
            "action": action,
        }

    phone = normalize_phone(caller_phone)
    slug = (business_slug or "").strip()
    if not slug:
        return {
            "ok": False,
            "error": "business_slug is required",
            "code": "slug_required",
            "auth_mode": mode,
        }
    if not phone:
        # Voice always has Twilio From; empty phone is admin/MCP/legacy tests.
        if mode == "strict":
            return {
                "ok": False,
                "error": "A verified caller phone is required to change a site.",
                "code": "phone_required",
                "auth_mode": mode,
            }
        return {
            "ok": True,
            "auth_level": "cid_legacy",
            "auth_mode": mode,
            "action": action,
            "note": "no_caller_phone",
        }

    matched = find_customers_for_phone(phone)
    claim = owners_of_slug(slug)

    if claim:
        for cust in claim:
            if phone_is_trusted(cust, phone):
                return {
                    "ok": True,
                    "auth_level": "cid_only",
                    "auth_mode": mode,
                    "customer": cust,
                    "action": action,
                }
        return {
            "ok": False,
            "error": (
                "This phone number is not authorized to update that business site. "
                "Call from the owner's registered line."
            ),
            "code": "not_owner",
            "auth_mode": mode,
            "slug": slug,
        }

    # No paid owner claims this slug yet.
    paid_here = [c for c in matched if is_owner_write_status(c.get("status"))]
    if paid_here:
        for cust in paid_here:
            owned = slugs_owned(cust)
            own_slug = (cust.get("slug") or "").strip()
            if own_slug in ("", slug) or slug in owned:
                return {
                    "ok": True,
                    "auth_level": "cid_only",
                    "auth_mode": mode,
                    "customer": cust,
                    "action": action,
                }
        return {
            "ok": False,
            "error": (
                "This line is registered to a different site. "
                "I can only take updates for your own business."
            ),
            "code": "slug_mismatch",
            "auth_mode": mode,
            "slug": slug,
        }

    non_paid = [c for c in matched if not is_owner_write_status(c.get("status"))]
    if non_paid:
        st = (non_paid[0].get("status") or "unknown").strip()
        return {
            "ok": False,
            "error": (
                "Site updates are available after the website is paid and activated. "
                f"Current account status is {st}."
            ),
            "code": "not_active_owner",
            "auth_mode": mode,
            "status": st,
        }

    # No registry hit for this phone and slug unclaimed.
    if mode == "strict":
        return {
            "ok": False,
            "error": (
                "I don't have an active owner account on this phone for that site yet."
            ),
            "code": "not_registered",
            "auth_mode": mode,
        }

    # soft + legacy catalog demos: allow unclaimed slug with no customer row
    return {
        "ok": True,
        "auth_level": "cid_legacy",
        "auth_mode": mode,
        "action": action,
    }


def set_trusted_phones(phone: str, phones: list[str]) -> dict[str, Any]:
    """Replace trusted_phones list (primary phone always remains trusted via helper)."""
    key = normalize_phone(phone)
    if not key:
        return {"ok": False, "error": "invalid phone number"}
    cleaned: list[str] = []
    seen: set[str] = set()
    for p in phones or []:
        n = normalize_phone(p)
        if n and n not in seen:
            seen.add(n)
            cleaned.append(n)
    return upsert(phone, patch={"trusted_phones": cleaned})


def record_voice_consent(
    phone: str,
    *,
    consent_version: str = "2026-08-14",
) -> dict[str, Any]:
    """Stamp biometric consent before enrollment (Phase 2)."""
    key = normalize_phone(phone)
    if not key:
        return {"ok": False, "error": "invalid phone number"}
    row = get(key)
    if not row:
        return {"ok": False, "error": "customer not found"}
    if not is_owner_write_status(row.get("status")):
        return {
            "ok": False,
            "error": "voice enrollment requires paid/active_owner status",
            "code": "not_active_owner",
        }
    va = dict(row.get("voice_auth") or {})
    va["consent_version"] = (consent_version or "2026-08-14").strip()
    va["consented_at"] = _now()
    return upsert(key, patch={"voice_auth": va})


def mark_voice_enrolled(
    phone: str,
    *,
    vendor: str = "none",
    template_id: str = "",
    quality: float | None = None,
    consent_version: str = "2026-08-14",
) -> dict[str, Any]:
    """Mark owner voice template enrolled (Phase 2+)."""
    key = normalize_phone(phone)
    if not key:
        return {"ok": False, "error": "invalid phone number"}
    row = get(key)
    if not row:
        return {"ok": False, "error": "customer not found"}
    if not is_owner_write_status(row.get("status")):
        return {
            "ok": False,
            "error": "voice enrollment requires paid/active_owner status",
            "code": "not_active_owner",
        }
    va = dict(row.get("voice_auth") or {})
    if not va.get("consented_at"):
        va["consent_version"] = (consent_version or "2026-08-14").strip()
        va["consented_at"] = _now()
    va["vendor"] = (vendor or "none").strip() or "none"
    va["template_id"] = (template_id or f"tmpl-{uuid.uuid4().hex[:12]}").strip()
    va["enrolled_at"] = _now()
    if quality is not None:
        va["quality"] = quality
    va["fail_streak"] = 0
    # Phase 4: template age baseline
    va["last_verify_at"] = None
    return upsert(key, patch={"voice_auth": va})


def clear_voice_auth(phone: str) -> dict[str, Any]:
    """Forget-me: drop voice template metadata."""
    key = normalize_phone(phone)
    if not key:
        return {"ok": False, "error": "invalid phone number"}
    return upsert(key, patch={"voice_auth": {}})


def touch_owner_call(
    phone: str,
    *,
    ani: str | None = None,
    at: str | None = None,
) -> dict[str, Any]:
    """Record last successful owner-desk call metadata (Phase 4 dormancy/ANI)."""
    key = normalize_phone(phone)
    if not key:
        return {"ok": False, "error": "invalid phone number"}
    row = get(key)
    if not row:
        return {"ok": False, "error": "customer not found"}
    va = dict(row.get("voice_auth") or {})
    ts = at or _now()
    va["last_call_at"] = ts
    nani = normalize_phone(ani) if ani else key
    if nani:
        va["last_ani"] = nani
    return upsert(key, patch={"voice_auth": va})


def record_voice_verify_result(
    phone: str,
    *,
    ok: bool,
    score: float | None = None,
    at: str | None = None,
) -> dict[str, Any]:
    """Update fail_streak / last_verify_at after an F2 window (Phase 4)."""
    key = normalize_phone(phone)
    if not key:
        return {"ok": False, "error": "invalid phone number"}
    row = get(key)
    if not row:
        return {"ok": False, "error": "customer not found"}
    va = dict(row.get("voice_auth") or {})
    ts = at or _now()
    if ok:
        va["fail_streak"] = 0
        va["last_verify_at"] = ts
        if score is not None:
            va["last_score"] = float(score)
    else:
        va["fail_streak"] = int(va.get("fail_streak") or 0) + 1
        va["last_fail_at"] = ts
    return upsert(key, patch={"voice_auth": va})


def list_customers(
    *,
    status: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    with _lock:
        rows = list(_read().values())
    if status:
        rows = [r for r in rows if r.get("status") == status]
    rows.sort(key=lambda r: r.get("updated_at") or r.get("created_at") or "", reverse=True)
    rows = rows[: max(1, min(int(limit or 200), 1000))]
    return {"ok": True, "count": len(rows), "customers": rows}


def upsert(
    phone: str,
    *,
    status: str | None = None,
    business_name: str = "",
    contact_name: str = "",
    email: str = "",
    category: str = "",
    source: str = "",
    requirements: dict | list | str | None = None,
    requirements_summary: str = "",
    demo_url: str = "",
    slug: str = "",
    stripe_payment_link: str = "",
    stripe_customer_id: str = "",
    notes: str = "",
    builder_brief_path: str = "",
    honcho_session_id: str = "",
    patch: dict | None = None,
) -> dict[str, Any]:
    key = normalize_phone(phone)
    if not key:
        return {"ok": False, "error": "invalid phone number"}

    with _lock:
        data = _read()
        _migrate_lifecycle_identity(data)
        row = dict(data.get(key) or {})
        if not row:
            row = {
                "id": f"cust-{uuid.uuid4().hex[:12]}",
                "phone": key,
                "created_at": _now(),
                "status": "prospect",
                "source": source or "unknown",
            }
        if status:
            if status not in STATUSES:
                return {
                    "ok": False,
                    "error": f"invalid status {status!r}",
                    "valid_statuses": STATUSES,
                }
            row["status"] = status
        if business_name:
            row["business_name"] = business_name.strip()
        if contact_name:
            row["contact_name"] = contact_name.strip()
        if email:
            row["email"] = email.strip()
        if category:
            row["category"] = category.strip()
        if source:
            row["source"] = source.strip()
        if requirements is not None:
            row["requirements"] = requirements
            row["requirements_updated_at"] = _now()
        if requirements_summary:
            row["requirements_summary"] = requirements_summary.strip()
        if demo_url:
            row["demo_url"] = demo_url.strip()
        if slug:
            row["slug"] = slug.strip()
        if stripe_payment_link:
            row["stripe_payment_link"] = stripe_payment_link.strip()
        if stripe_customer_id:
            row["stripe_customer_id"] = stripe_customer_id.strip()
        if notes:
            existing = row.get("notes") or ""
            row["notes"] = (existing + "\n" + notes).strip() if existing else notes
        if builder_brief_path:
            row["builder_brief_path"] = builder_brief_path.strip()
        if honcho_session_id:
            row["honcho_session_id"] = honcho_session_id.strip()
        if patch:
            for k, v in patch.items():
                if k in ("id", "phone", "created_at"):
                    continue
                row[k] = v
        if is_owner_write_status(row.get("status")):
            if not isinstance(row.get("account_id"), str) or not row.get("account_id", "").strip():
                row["account_id"] = f"acct_{uuid.uuid4().hex}"
            revision = row.get("auth_revision")
            if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
                row["auth_revision"] = 0
        row["updated_at"] = _now()
        data[key] = row
        _write(data)
        return {"ok": True, "customer": dict(row)}


def register_callback(
    phone: str,
    *,
    business_name: str = "",
    contact_name: str = "",
    email: str = "",
    source: str = "ai411_web",
    notes: str = "",
) -> dict[str, Any]:
    """Public web signup: queue a callback. resume_web is NOT website onboarding."""
    src = (source or "ai411_web").strip() or "ai411_web"
    status = "resume_waitlist" if src == "resume_web" else "callback_queued"
    note = f"Callback requested via {src} at {_now()}"
    extra = (notes or "").strip()
    if extra:
        note = note + "\n" + extra
    return upsert(
        phone,
        status=status,
        business_name=business_name,
        contact_name=contact_name,
        email=email,
        source=src,
        notes=note,
    )


def save_requirements(
    phone: str,
    *,
    requirements: dict | list | str,
    summary: str = "",
    business_name: str = "",
    category: str = "",
    email: str = "",
    mark_ready: bool = True,
) -> dict[str, Any]:
    """Persist interview output; optionally flip status → requirements_ready."""
    status = "requirements_ready" if mark_ready else "onboarding"
    return upsert(
        phone,
        status=status,
        business_name=business_name,
        category=category,
        email=email,
        requirements=requirements,
        requirements_summary=summary,
    )


def mark_paid(phone: str, *, stripe_customer_id: str = "") -> dict[str, Any]:
    return upsert(
        phone,
        status="active_owner",
        stripe_customer_id=stripe_customer_id,
        notes=f"Marked paid at {_now()}",
    )


def mark_demo_ready(
    phone: str,
    *,
    demo_url: str,
    slug: str = "",
    stripe_payment_link: str = "",
) -> dict[str, Any]:
    return upsert(
        phone,
        status="demo_ready",
        demo_url=demo_url,
        slug=slug,
        stripe_payment_link=stripe_payment_link,
    )


def resolve_mode(
    phone: str,
    *,
    direction: str = "inbound",
    outbound_sales_slug: str | None = None,
    env_mode: str = "auto",
    in_sales_outreach: bool = False,
) -> str:
    """Pick agent mode for this phone.

    env_mode other than 'auto' pins the process (legacy single-mode deploy).
    When auto:
      - paid/active_owner → owner_updates
      - onboarding funnel → onboarding
      - demo/sales ready → sales
      - outbound dialer with explicit slug → sales
      - phone on cold outreach list (inbound rare) → sales only if flagged
      - else → ai411
    """
    pinned = (env_mode or "auto").strip().lower()
    if pinned and pinned not in ("auto", "unified"):
        return pinned

    # Explicit outbound sales campaign always sales for that call.
    if direction == "outbound" and outbound_sales_slug:
        return MODE_SALES

    cust = get(phone)
    if cust:
        st = (cust.get("status") or "").strip()
        if st in ("paid", "active_owner"):
            return MODE_OWNER
        if st == "resume_waitlist":
            return MODE_AI411
        if st in ("onboarding", "callback_queued", "prospect"):
            # prospect/callback → run onboarding interview when they connect
            return MODE_ONBOARDING
        if st in (
            "requirements_ready",
            "building",
            "demo_ready",
            "sales_ready",
        ):
            return MODE_SALES
        if st == "do_not_call":
            return MODE_AI411  # polite directory only; no pitch

    if in_sales_outreach and direction == "outbound":
        return MODE_SALES

    # Unified still means AI411 base + owner when registry says so;
    # unknown numbers stay AI411.
    return MODE_AI411


def write_builder_brief(
    phone: str,
    *,
    briefs_dir: Path | None = None,
) -> dict[str, Any]:
    """Write a markdown brief for the coding agent from stored requirements."""
    cust = get(phone)
    if not cust:
        return {"ok": False, "error": "customer not found"}
    req = cust.get("requirements")
    if not req and not cust.get("requirements_summary"):
        return {"ok": False, "error": "no requirements on file"}

    root = briefs_dir or (
        Path(os.getenv("BUILDER_BRIEFS_DIR", ""))
        if os.getenv("BUILDER_BRIEFS_DIR")
        else _REPO / "data" / "builder-briefs"
    )
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    slug = (cust.get("slug") or cust.get("business_name") or cust["phone"]).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-") or "customer"
    path = root / f"{slug}-{cust['id']}.md"

    req_block = (
        json.dumps(req, indent=2, ensure_ascii=False)
        if not isinstance(req, str)
        else req
    )
    body = f"""# Website build brief

- customer_id: {cust.get("id")}
- phone: {cust.get("phone")}
- business: {cust.get("business_name") or "(tbd)"}
- contact: {cust.get("contact_name") or ""}
- email: {cust.get("email") or ""}
- category: {cust.get("category") or ""}
- status: {cust.get("status")}
- generated_at: {_now()}

## Summary

{cust.get("requirements_summary") or "(see structured requirements)"}

## Structured requirements

```json
{req_block}
```

## Agent instructions

1. Create or update `generated-sites/{slug}.html` following demo-websites landing rules
   (NAP truth, no invented phone/hours, self-contained HTML).
2. Match FMWS craft rubric; mobile-first; Hours/Address/tel hooks for owner_updates.
3. When done, call mark_demo_ready API / customers.mark_demo_ready with the live URL.
4. Do not invent NAP; leave placeholders only if the interview did not collect them.
"""
    path.write_text(body, encoding="utf-8")
    upsert(phone, builder_brief_path=str(path), status="building")
    return {"ok": True, "path": str(path), "slug": slug, "customer_id": cust.get("id")}
