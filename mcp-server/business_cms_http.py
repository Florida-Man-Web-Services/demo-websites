"""HTTP surface for public business pages and the owner CMS.

Stdlib dispatcher so mcp-server tests do not require FastAPI. voice-agent
mounts FastAPI wrappers in the integration hub.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, unquote

import business_cms_auth as cms_auth
import business_cms_schema as schema
import business_cms_store as store
import business_cms_render as render
import business_inbox as inbox
from business_cms_auth import CmsAuthError
from business_cms_store import CmsStoreError
from business_inbox import InboxError

_SLUG_RE = re.compile(r"^/businesses/([a-z0-9]+(?:-[a-z0-9]+)*)/?$")
_REQ_RE = re.compile(r"^/businesses/([a-z0-9]+(?:-[a-z0-9]+)*)/requests/?$")
_CMS_PAGE = re.compile(r"^/cms/businesses/([a-z0-9]+(?:-[a-z0-9]+)*)/?$")
_API = re.compile(r"^/api/business-cms/([a-z0-9]+(?:-[a-z0-9]+)*)/(session|draft|preview|publish|versions|restore|inbox)(?:/([a-z0-9-]+))?$")
CSRF_COOKIE = "fmws_cms_csrf"
ORIGIN_ENV = "BUSINESS_CMS_PUBLIC_ORIGIN"


@dataclass
class CmsResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    cookies: dict[str, str]


def public_origin() -> str:
    return (os.getenv(ORIGIN_ENV) or "http://127.0.0.1").rstrip("/")


def _json(status: int, payload: dict[str, Any], *, cookies: dict[str, str] | None = None) -> CmsResponse:
    return CmsResponse(
        status=status,
        headers={"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"},
        body=json.dumps(payload).encode("utf-8"),
        cookies=cookies or {},
    )


def _html(status: int, text: str, *, etag: str | None = None, cookies: dict[str, str] | None = None) -> CmsResponse:
    headers = {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"}
    if etag:
        headers["ETag"] = etag
    return CmsResponse(status=status, headers=headers, body=text.encode("utf-8"), cookies=cookies or {})


def _cookie_map(header: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not header:
        return out
    for part in header.split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            out[key.strip()] = unquote(value.strip())
    return out


def _csrf_ok(method: str, cookies: dict[str, str], headers: dict[str, str], form: dict[str, str]) -> bool:
    if method in {"GET", "HEAD"}:
        return True
    expected = cookies.get(CSRF_COOKIE) or ""
    got = headers.get("x-csrf-token") or form.get("csrf_token") or ""
    return bool(expected) and secrets.compare_digest(expected, got)


def dispatch(
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | str | None = None,
    cookie_header: str | None = None,
) -> CmsResponse:
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    cookies = _cookie_map(cookie_header or headers.get("cookie"))
    raw = body if isinstance(body, bytes) else (body or "").encode("utf-8")
    form: dict[str, str] = {}
    payload: Any = None
    ctype = headers.get("content-type") or ""
    if raw and "application/json" in ctype:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _json(400, {"ok": False, "code": "invalid_json"})
    elif raw and "application/x-www-form-urlencoded" in ctype:
        parsed = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
        form = {k: v[-1] for k, v in parsed.items()}
        payload = form
    method = method.upper()
    if not store.cms_enabled() and path.startswith(("/businesses/", "/cms/", "/api/business-cms/")):
        return _json(404, {"ok": False, "code": "feature_disabled"})

    match = _SLUG_RE.match(path)
    if match and method == "GET":
        return _public_page(match.group(1))
    match = _REQ_RE.match(path)
    if match and method == "POST":
        return _public_request(match.group(1), payload if isinstance(payload, dict) else form)
    match = _CMS_PAGE.match(path)
    if match and method == "GET":
        return _owner_page(match.group(1), cookies)
    match = _API.match(path)
    if match:
        slug, action, extra = match.group(1), match.group(2), match.group(3)
        return _owner_api(method, slug, action, extra, cookies, headers, payload if isinstance(payload, dict) else form, form)
    return _json(404, {"ok": False, "code": "not_found"})


def _public_page(slug: str) -> CmsResponse:
    live = store.current_release(slug)
    if live is None or not live.get("html"):
        return CmsResponse(status=404, headers={"Cache-Control": "no-store"}, body=b"", cookies={})
    etag = f'"{live["release_id"]}"'
    return _html(200, live["html"], etag=etag)


def _public_request(slug: str, data: dict[str, Any]) -> CmsResponse:
    kind = str(data.get("kind") or "contact")
    try:
        result = inbox.create_request(
            slug,
            kind=kind if kind in inbox.KINDS else "contact",
            message=str(data.get("message") or data.get("requested_time_text") or ""),
            source="public_form",
            requested_time_text=data.get("requested_time_text"),
            idempotency_key=str(data.get("idempotency_key") or "") or None,
        )
    except (InboxError, schema.CmsValidationError, CmsStoreError) as exc:
        code = getattr(exc, "code", "invalid")
        return _json(400, {"ok": False, "code": code})
    return _json(200, inbox.visitor_receipt(result["request"]))


def _owner_page(slug: str, cookies: dict[str, str]) -> CmsResponse:
    from html import escape as esc

    try:
        cms_auth.require_owner(slug, cookies.get(cms_auth.COOKIE_NAME), "cms_draft")
    except (CmsAuthError, schema.CmsValidationError):
        return _html(401, "<!DOCTYPE html><html><body>Sign in required.</body></html>")
    draft = store.load_draft(slug) or schema.empty_document(slug)
    csrf = cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(16)
    state = store.load_state(slug)
    name = esc((draft.get("identity") or {}).get("name") or slug)
    page = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/><title>CMS {esc(slug)}</title></head>
<body>
<h1>CMS for {name}</h1>
<p>Draft revision: {esc(str(state.get('draft_revision') or 'none'))}</p>
<p>Published: {esc(str(state.get('published_release') or 'none'))}</p>
<p>Wave-1 editors: identity, hours, services, FAQ, homepage, SEO. Staff/specials/events/media stay draft-only.</p>
</body></html>
"""
    return _html(200, page, cookies={CSRF_COOKIE: csrf})


def _owner_api(
    method: str,
    slug: str,
    action: str,
    extra: str | None,
    cookies: dict[str, str],
    headers: dict[str, str],
    payload: dict[str, Any],
    form: dict[str, str],
) -> CmsResponse:
    if action == "session" and method == "POST":
        try:
            token = cms_auth.issue_owner_session(
                slug,
                account_id=str(payload.get("account_id") or "owner"),
                actor=str(payload.get("actor") or payload.get("account_id") or "owner"),
            )
        except CmsAuthError as exc:
            return _json(401, cms_auth.denied(exc.code))
        csrf = secrets.token_urlsafe(16)
        return _json(200, {"ok": True}, cookies={cms_auth.COOKIE_NAME: token, CSRF_COOKIE: csrf})
    if action == "session" and method == "DELETE":
        return _json(200, {"ok": True}, cookies={cms_auth.COOKIE_NAME: "", CSRF_COOKIE: ""})
    if not _csrf_ok(method, cookies, headers, form):
        return _json(403, {"ok": False, "code": "csrf"})
    needed = {
        "draft": "cms_draft",
        "preview": "cms_preview",
        "publish": "cms_publish",
        "versions": "cms_draft",
        "restore": "cms_restore",
        "inbox": "cms_inbox_read" if method == "GET" else "cms_inbox_write",
    }[action]
    try:
        principal = cms_auth.require_owner(slug, cookies.get(cms_auth.COOKIE_NAME), needed)
    except CmsAuthError as exc:
        return _json(401, cms_auth.denied(exc.code))
    try:
        if action == "draft" and method == "GET":
            return _json(200, {"ok": True, "document": store.load_draft(slug) or schema.empty_document(slug), "state": store.load_state(slug)})
        if action == "draft" and method == "PUT":
            expected = payload.get("expected_draft_rev")
            doc = payload.get("document") or payload
            saved = store.save_draft(slug, doc, expected_draft_rev=expected, actor=principal.actor)
            return _json(200, saved)
        if action == "preview" and method == "POST":
            doc = store.load_draft(slug) or schema.empty_document(slug)
            html_out = render.render_preview_html(doc, origin=public_origin(), draft_revision=str(store.load_state(slug).get("draft_revision") or "none"))
            return _html(200, html_out)
        if action == "publish" and method == "POST":
            expected_draft = payload.get("expected_draft_rev") or store.load_state(slug).get("draft_revision")
            if not expected_draft:
                return _json(400, {"ok": False, "code": "missing_draft"})
            expected_release = payload.get("expected_release_id")
            if "expected_release_id" not in payload:
                expected_release = store.load_state(slug).get("published_release")
            draft = store.load_draft(slug)
            if draft is None:
                return _json(400, {"ok": False, "code": "missing_draft"})
            html_out = render.render_public_html(draft, origin=public_origin(), release_id="pending")
            result = store.publish(
                slug,
                expected_draft_rev=expected_draft,
                expected_release_id=expected_release,
                actor=principal.actor,
                html=html_out,
            )
            html_final = render.render_public_html(draft, origin=public_origin(), release_id=result["release_id"])
            folder = store.tenant_dir(slug) / "releases" / result["release_id"] / "index.html"
            folder.write_text(html_final, encoding="utf-8")
            return _json(200, result)
        if action == "versions" and method == "GET":
            return _json(200, {"ok": True, "versions": store.list_versions(slug)})
        if action == "restore" and method == "POST":
            restored = store.restore_to_draft(slug, str(payload.get("release_id") or extra or ""), actor=principal.actor)
            return _json(200, restored)
        if action == "inbox" and method == "GET":
            return _json(200, {"ok": True, "requests": inbox.list_requests(slug)})
        if action == "inbox" and extra and method == "PATCH":
            return _json(200, inbox.patch_request(slug, extra, status=str(payload.get("status") or "acknowledged")))
        if action == "inbox" and extra and method == "DELETE":
            return _json(200, inbox.delete_request(slug, extra))
    except CmsStoreError as exc:
        status = 409 if exc.code.startswith("stale") else 400
        return _json(status, {"ok": False, "code": exc.code})
    except (schema.CmsValidationError, InboxError) as exc:
        return _json(400, {"ok": False, "code": getattr(exc, "code", "invalid")})
    return _json(405, {"ok": False, "code": "method"})
