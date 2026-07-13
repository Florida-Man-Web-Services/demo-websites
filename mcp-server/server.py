"""MCP server for Florida Man Web Services sales/support agents.

Streamable HTTP transport, bearer-token auth (except /health), business tools.
Run: MCP_AUTH_TOKEN=... python server.py   → http://0.0.0.0:8036/mcp
"""

import contextlib
import functools
import hmac
import logging
import os
import sys
from pathlib import Path

# voice-agent/ holds the shared data layer (businesses.py, config.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "voice-agent"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import anyio
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route

import businesses
import callers as callers_mod
import config
from calllog import append_outcome, history_for
from knowledge import (
    get_business_snapshot as knowledge_snapshot,
    search_business_knowledge as knowledge_search,
)
from changerequests import (
    cancel_change_request as cancel_change_request_sync,
    create_change_request as create_change_request_sync,
    get_site_outline as get_site_outline_sync,
    list_open_change_requests as list_open_change_requests_sync,
)
from lookup import find_business
from pitch import get_pitch

logger = logging.getLogger("demo-mcp")


def _build_transport_security() -> TransportSecuritySettings:
    """Explicit Host/Origin allowlist for the DNS-rebinding-protection
    middleware the SDK enables by default (fastmcp/server.py ~177-183).

    Without this, FastMCP's default host="127.0.0.1" triggers an auto
    allowlist of only 127.0.0.1/localhost/::1 — any production request with
    Host: mcp.flmanbiosci.net gets a 421. TransportSecurityMiddleware
    (mcp.server.transport_security) matches a Host header either by exact
    string or, for entries ending ":*", by "base_host:" prefix — so a bare
    hostname entry only matches a Host header with no port, and a ":*"
    entry matches any port on that host.
    """
    hosts_env = os.getenv(
        "MCP_ALLOWED_HOSTS",
        "mcp.flmanbiosci.net,mcp.flmanbiosci.net:443,"
        "localhost,localhost:*,127.0.0.1,127.0.0.1:*",
    )
    hosts = [h.strip() for h in hosts_env.split(",") if h.strip()]

    def _origin_for(host: str) -> str:
        local = host.startswith("localhost") or host.startswith("127.0.0.1")
        scheme = "http" if local else "https"
        return f"{scheme}://{host}"

    origins = [_origin_for(h) for h in hosts]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


mcp = FastMCP(
    "florida-man-web-services",
    stateless_http=True,
    json_response=True,
    transport_security=_build_transport_security(),
)


def _lookup_business_sync(query: str) -> dict:
    try:
        return find_business(query)
    except Exception as e:  # keep failures speakable, never raise
        logger.exception("tool %s failed", "lookup_business")
        return {"found": False, "suggestions": [], "error": _unavailable(e)}


@mcp.tool()
async def lookup_business(query: str) -> dict:
    """Look up a Gainesville business by name, slug, or phone number.

    Returns the business profile including its live demo website URL, or
    {"found": false, "suggestions": [...]} with close matches to offer the
    caller ("did you mean ...?"). If multiple businesses share a phone
    number the result is ambiguous_phone with the candidates — ask the
    caller which business they are.
    """
    return await anyio.to_thread.run_sync(_lookup_business_sync, query)


def _get_pitch_info_sync() -> dict:
    try:
        return get_pitch()
    except Exception as e:
        logger.exception("tool %s failed", "get_pitch_info")
        return {"error": _unavailable(e)}


@mcp.tool()
async def get_pitch_info() -> dict:
    """The Florida Man Web Services sales cheat sheet: the offer and price,
    objection-handling lines, compliance rules you must follow on every
    call, and what to do instead of texting (this server cannot send SMS).
    """
    return await anyio.to_thread.run_sync(_get_pitch_info_sync)


def _get_call_history_sync(business: str) -> dict:
    try:
        hit = find_business(business)
        if not hit.get("found"):
            return {"found": False, "suggestions": hit.get("suggestions", [])}
        return {"found": True, "slug": hit["slug"], "calls": history_for(hit["slug"])}
    except Exception as e:
        logger.exception("tool %s failed", "get_call_history")
        return {"found": False, "suggestions": [], "error": _unavailable(e)}


@mcp.tool()
async def get_call_history(business: str) -> dict:
    """Past call-log entries for a business (by name, slug, or phone), oldest
    first — check before pitching so you know prior contact and outcomes.
    Covers only calls logged through this server."""
    return await anyio.to_thread.run_sync(_get_call_history_sync, business)


def _log_call_outcome_sync(
    business: str,
    outcome: str,
    notes: str,
    email: str = "",
    callback_time: str = "",
    caller_phone: str = "",
    direction: str = "",
) -> dict:
    try:
        hit = find_business(business)
        if not hit.get("found"):
            return {
                "logged": False,
                "error": f"unknown business {business!r}",
                "suggestions": hit.get("suggestions", []),
            }
        return append_outcome(
            businesses.by_slug(hit["slug"]),
            outcome,
            notes,
            email,
            callback_time,
            caller_phone,
            direction,
        )
    except Exception as e:
        logger.exception("tool %s failed", "log_call_outcome")
        return {"logged": False, "error": _unavailable(e)}


@mcp.tool()
async def log_call_outcome(
    business: str,
    outcome: str,
    notes: str,
    email: str = "",
    callback_time: str = "",
    caller_phone: str = "",
    direction: str = "",
) -> dict:
    """Record how the call went. Call exactly once near the end of every
    call. Outcomes: interested, wants_email, callback_requested, sent_sms,
    not_interested, do_not_call, wrong_number, voicemail, other. Use
    do_not_call whenever the person asks not to be contacted again.
    caller_phone: the phone number the caller is actually calling from, if
    known — REQUIRED for do_not_call so the right number is protected.
    direction: 'inbound' or 'outbound' if known."""
    return await anyio.to_thread.run_sync(
        functools.partial(
            _log_call_outcome_sync,
            business,
            outcome,
            notes,
            email,
            callback_time,
            caller_phone,
            direction,
        )
    )


def _search_business_knowledge_sync(query: str, limit: int = 5) -> dict:
    try:
        return knowledge_search(query, limit=limit)
    except Exception as e:
        logger.exception("tool %s failed", "search_business_knowledge")
        return {"ok": False, "results": [], "error": _unavailable(e)}


@mcp.tool()
async def search_business_knowledge(query: str, limit: int = 5) -> dict:
    """Search local demo-site knowledge for facts about Gainesville businesses.

    Indexes generated-sites HTML (no live crawl). Returns ranked text snippets
    with slug/title and fetched_at (file mtime). Use when the caller asks
    about services, hours language on the page, address details, or anything
    beyond the short lookup_business profile. Scoring is keyword/TF-IDF v1
    and may be swapped for embeddings later.
    """
    return await anyio.to_thread.run_sync(
        functools.partial(_search_business_knowledge_sync, query, limit)
    )


def _get_business_snapshot_sync(slug: str) -> dict:
    try:
        return knowledge_snapshot(slug)
    except Exception as e:
        logger.exception("tool %s failed", "get_business_snapshot")
        return {"found": False, "error": _unavailable(e)}


@mcp.tool()
async def get_business_snapshot(slug: str) -> dict:
    """Load a compact text snapshot of one business demo page by slug.

    Prefer after lookup_business when you need the fuller page content
    (about, services, contact copy). Slug is the generated-sites filename
    stem (e.g. aaa-refrigeration). fetched_at is the HTML file mtime.
    """
    return await anyio.to_thread.run_sync(_get_business_snapshot_sync, slug)


def _get_caller_profile_sync(phone: str) -> dict:
    try:
        return callers_mod.get_profile(phone)
    except Exception as e:
        logger.exception("tool %s failed", "get_caller_profile")
        return {"found": False, "error": _unavailable(e)}


@mcp.tool()
async def get_caller_profile(phone: str) -> dict:
    """Load a caller's remembered profile by phone (E.164 or US 10-digit).

    When consent.memory_ok is false, only phone + consent (and timestamps)
    are returned — names, preferences, notes, and last_topics are redacted.
    Use after greeting to personalize if memory is allowed.
    """
    return await anyio.to_thread.run_sync(_get_caller_profile_sync, phone)


def _update_caller_profile_sync(phone: str, patch: dict | None = None) -> dict:
    try:
        return callers_mod.update_profile(phone, patch)
    except Exception as e:
        logger.exception("tool %s failed", "update_caller_profile")
        return {"updated": False, "error": _unavailable(e)}


@mcp.tool()
async def update_caller_profile(phone: str, patch: dict | None = None) -> dict:
    """Create or merge-patch a caller profile by phone.

    patch may include display_name, preferred_name, preferences (interests,
    avoid, preferred_areas, sms_ok, mobility, accessibility), last_topics,
    last_call_at, consent (memory_ok, marketing_ok). Creates the profile if
    missing. Nested preference/consent keys are shallow-merged.
    """
    return await anyio.to_thread.run_sync(
        functools.partial(_update_caller_profile_sync, phone, patch)
    )


def _forget_caller_sync(phone: str) -> dict:
    try:
        return callers_mod.forget_profile(phone)
    except Exception as e:
        logger.exception("tool %s failed", "forget_caller")
        return {"forgotten": False, "error": _unavailable(e)}


@mcp.tool()
async def forget_caller(phone: str) -> dict:
    """Permanently delete a caller profile (hard delete — \"forget me\").

    Idempotent: returns forgotten=true even if no profile existed.
    """
    return await anyio.to_thread.run_sync(_forget_caller_sync, phone)


def _add_caller_note_sync(phone: str, note: str) -> dict:
    try:
        return callers_mod.add_note(phone, note)
    except Exception as e:
        logger.exception("tool %s failed", "add_caller_note")
        return {"added": False, "error": _unavailable(e)}


@mcp.tool()
async def add_caller_note(phone: str, note: str) -> dict:
    """Append a short freeform note to the caller's profile (creates if needed).

    Notes are only returned by get_caller_profile when consent.memory_ok is
    true. Use for durable call takeaways the caller wants remembered.
    """
    return await anyio.to_thread.run_sync(
        functools.partial(_add_caller_note_sync, phone, note)
    )


def _create_change_request_sync(
    business_slug: str,
    summary: str,
    items: str = "[]",
    caller_phone: str = "",
    source: str = "voice",
    confirmation_spoken: bool = True,
    priority: str = "normal",
    call_sid: str = "",
    transcript_ref: str = "",
) -> dict:
    try:
        # MCP clients often pass structured args as JSON strings.
        parsed_items: object = items
        if isinstance(items, str):
            parsed_items = items
        return create_change_request_sync(
            business_slug=business_slug,
            summary=summary,
            items=parsed_items,
            caller_phone=caller_phone,
            source=source,
            confirmation_spoken=confirmation_spoken,
            priority=priority,
            call_sid=call_sid,
            transcript_ref=transcript_ref,
        )
    except Exception as e:
        logger.exception("tool %s failed", "create_change_request")
        return {"created": False, "error": _unavailable(e)}


@mcp.tool()
async def create_change_request(
    business_slug: str,
    summary: str,
    items: str = "[]",
    caller_phone: str = "",
    source: str = "voice",
    confirmation_spoken: bool = True,
    priority: str = "normal",
    call_sid: str = "",
    transcript_ref: str = "",
) -> dict:
    """Create a pending owner site ChangeRequest for a business slug.

    items: JSON array string of objects like
    {"type":"hours|copy|phone|...","target":"...","after":"...","before?":"...","notes?":"..."}.
    Call after the owner confirms the change list on the phone.
    """
    return await anyio.to_thread.run_sync(
        functools.partial(
            _create_change_request_sync,
            business_slug,
            summary,
            items,
            caller_phone,
            source,
            confirmation_spoken,
            priority,
            call_sid,
            transcript_ref,
        )
    )


def _list_open_change_requests_sync(slug: str = "") -> dict:
    try:
        return list_open_change_requests_sync(slug or None)
    except Exception as e:
        logger.exception("tool %s failed", "list_open_change_requests")
        return {"count": 0, "requests": [], "error": _unavailable(e)}


@mcp.tool()
async def list_open_change_requests(slug: str = "") -> dict:
    """List open (non-terminal) ChangeRequests. Optional slug filters to one business."""
    return await anyio.to_thread.run_sync(_list_open_change_requests_sync, slug)


def _cancel_change_request_sync(request_id: str) -> dict:
    try:
        return cancel_change_request_sync(request_id)
    except Exception as e:
        logger.exception("tool %s failed", "cancel_change_request")
        return {"cancelled": False, "error": _unavailable(e)}


@mcp.tool()
async def cancel_change_request(request_id: str) -> dict:
    """Cancel a pending/open ChangeRequest by id (status → cancelled)."""
    return await anyio.to_thread.run_sync(_cancel_change_request_sync, request_id)


def _get_site_outline_tool_sync(slug: str) -> dict:
    try:
        return get_site_outline_sync(slug)
    except Exception as e:
        logger.exception("tool %s failed", "get_site_outline")
        return {"found": False, "error": _unavailable(e)}


@mcp.tool()
async def get_site_outline(slug: str) -> dict:
    """Read-only outline of generated-sites/<slug>.html: page title and headings.

    Use before capturing change requests so you know current sections/hours/CTAs.
    """
    return await anyio.to_thread.run_sync(_get_site_outline_tool_sync, slug)


def _unavailable(e: Exception) -> str:
    return (
        f"data unavailable ({e.__class__.__name__}) — apologize and offer "
        "the owner's callback number from get_pitch_info"
    )


class BearerAuth:
    """401 everything reaching this middleware unless Authorization: Bearer
    matches. /health bypasses auth by being a sibling Route mounted outside
    this middleware (see build_app), not via a path exemption here."""

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            auth = next(
                (v.decode() for k, v in scope.get("headers", [])
                 if k == b"authorization"),
                "",
            )
            if not self.token or not hmac.compare_digest(auth, f"Bearer {self.token}"):
                await JSONResponse(
                    {"error": "unauthorized"}, status_code=401
                )(scope, receive, send)
                return
        await self.app(scope, receive, send)


async def health(request):
    return PlainTextResponse("ok")


def build_app() -> Starlette:
    token = os.getenv("MCP_AUTH_TOKEN", "")
    inner = mcp.streamable_http_app()  # serves at /mcp within this sub-app

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield

    return Starlette(
        routes=[
            Route("/health", health),
            Mount("/", app=BearerAuth(inner, token)),
        ],
        lifespan=lifespan,
    )


def main():
    if not os.getenv("MCP_AUTH_TOKEN"):
        raise SystemExit("MCP_AUTH_TOKEN must be set")
    if not config.OWNER_CALLBACK_NUMBER:
        logger.warning(
            "OWNER_CALLBACK_NUMBER is empty — callers who ask for a human "
            "callback won't get a number to call"
        )
    uvicorn.run(
        build_app(), host="0.0.0.0", port=int(os.getenv("PORT", "8036"))
    )


if __name__ == "__main__":
    main()
