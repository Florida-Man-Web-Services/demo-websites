"""CMS owner authority. CID is not proof. Capabilities never include OTP or phones.

Browser sessions are HMAC cookies bound to tenant slug, account id, expiry,
and allowed actions. Every mutation re-checks paid/active_owner eligibility
against the customer registry.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Iterable

import customers
from business_cms_schema import canonical_slug

CMS_AUDIENCE = "fmws-business-cms"
CMS_ACTIONS = frozenset(
    {
        "cms_session",
        "cms_draft",
        "cms_preview",
        "cms_publish",
        "cms_restore",
        "cms_inbox_read",
        "cms_inbox_write",
    }
)
_ENV_KEY = "BUSINESS_CMS_SESSION_KEY"
_FALLBACK_KEY = "ACCOUNT_LIFECYCLE_SERVICE_KEY"
COOKIE_NAME = "fmws_cms_session"


class CmsAuthError(RuntimeError):
    def __init__(self, code: str, message: str = "cms authorization failed"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CmsPrincipal:
    slug: str
    account_id: str
    actor: str
    exp: int
    actions: tuple[str, ...]


def _key() -> bytes:
    value = (os.getenv(_ENV_KEY) or os.getenv(_FALLBACK_KEY) or "").encode("utf-8")
    return value


def _enc(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _dec(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def tenant_eligible(slug: str) -> bool:
    slug = canonical_slug(slug)
    owners = customers.owners_of_slug(slug)
    return any(customers.is_owner_write_status(row.get("status")) for row in owners)


def owner_account_for_slug(slug: str, account_id: str | None = None) -> dict[str, Any] | None:
    slug = canonical_slug(slug)
    owners = customers.owners_of_slug(slug)
    for row in owners:
        if not customers.is_owner_write_status(row.get("status")):
            continue
        if account_id and str(row.get("id") or row.get("account_id") or "") != account_id:
            continue
        return row
    return None


def issue_owner_session(
    slug: str,
    *,
    account_id: str,
    actor: str,
    ttl: int = 3600,
    actions: Iterable[str] | None = None,
) -> str:
    key = _key()
    if not key:
        raise CmsAuthError("key_unset", "cms session key is not configured")
    slug = canonical_slug(slug)
    if not tenant_eligible(slug):
        raise CmsAuthError("not_active_owner")
    owner = owner_account_for_slug(slug)
    if owner is None:
        raise CmsAuthError("not_active_owner")
    allowed = tuple(sorted(set(actions or CMS_ACTIONS)))
    if any(action not in CMS_ACTIONS for action in allowed):
        raise CmsAuthError("invalid_action")
    now = int(time.time())
    payload = {
        "v": 1,
        "aud": CMS_AUDIENCE,
        "slug": slug,
        "account_id": account_id,
        "actor": actor,
        "iat": now,
        "exp": now + max(60, min(int(ttl), 12 * 3600)),
        "nonce": secrets.token_urlsafe(12),
        "actions": list(allowed),
    }
    encoded = _enc(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = _enc(hmac.new(key, encoded.encode("ascii"), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def parse_session(token: str | None) -> dict[str, Any] | None:
    key = _key()
    if not key or not isinstance(token, str) or token.count(".") != 1:
        return None
    encoded, signature = token.split(".", 1)
    expected = _enc(hmac.new(key, encoded.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_dec(encoded))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    now = int(time.time())
    if (
        payload.get("v") != 1
        or payload.get("aud") != CMS_AUDIENCE
        or not isinstance(payload.get("exp"), int)
        or payload["exp"] < now
        or not isinstance(payload.get("slug"), str)
        or not isinstance(payload.get("account_id"), str)
        or not isinstance(payload.get("actions"), list)
    ):
        return None
    return payload


def require_owner(slug: str, token: str | None, action: str) -> CmsPrincipal:
    slug = canonical_slug(slug)
    if action not in CMS_ACTIONS:
        raise CmsAuthError("invalid_action")
    payload = parse_session(token)
    if payload is None:
        raise CmsAuthError("invalid_session")
    if payload.get("slug") != slug:
        raise CmsAuthError("wrong_tenant")
    allowed_actions = payload.get("actions") or []
    if action not in allowed_actions:
        raise CmsAuthError("wrong_purpose")
    if not tenant_eligible(slug):
        raise CmsAuthError("not_active_owner")
    return CmsPrincipal(
        slug=slug,
        account_id=str(payload["account_id"]),
        actor=str(payload.get("actor") or payload["account_id"]),
        exp=int(payload["exp"]),
        actions=tuple(payload["actions"]),
    )


def denied(code: str) -> dict[str, Any]:
    return {"ok": False, "error": "unauthorized", "code": code}
