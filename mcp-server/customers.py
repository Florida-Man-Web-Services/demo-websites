"""Customer lifecycle registry for FMWS voice + AI 411 onboarding.

Statuses drive per-call AGENT_MODE routing when AGENT_MODE=auto (recommended):

  unknown / no row          → ai411          (default public line)
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
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
        row = _read().get(key)
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
        rows = list(_read().values())
    return [dict(r) for r in rows if phone_is_trusted(r, key)]


def owners_of_slug(slug: str | None) -> list[dict[str, Any]]:
    """Paid/active_owner customers whose slug matches (case-sensitive store)."""
    s = (slug or "").strip()
    if not s:
        return []
    with _lock:
        rows = list(_read().values())
    out: list[dict[str, Any]] = []
    for r in rows:
        if not is_owner_write_status(r.get("status")):
            continue
        if (r.get("slug") or "").strip() == s:
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
            own_slug = (cust.get("slug") or "").strip()
            if own_slug in ("", slug):
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
) -> dict[str, Any]:
    """Public web signup: queue an onboarding call."""
    return upsert(
        phone,
        status="callback_queued",
        business_name=business_name,
        contact_name=contact_name,
        email=email,
        source=source,
        notes=f"Callback requested via {source} at {_now()}",
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
