"""MCP server for Florida Man Web Services sales/support agents.

Streamable HTTP transport, bearer-token auth (except /health), four tools.
Run: MCP_AUTH_TOKEN=... python server.py   → http://0.0.0.0:8036/mcp
"""

import contextlib
import os
import sys
from pathlib import Path

# voice-agent/ holds the shared data layer (businesses.py, config.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "voice-agent"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route

from calllog import append_outcome, history_for
from lookup import find_business
from pitch import get_pitch

mcp = FastMCP(
    "florida-man-web-services", stateless_http=True, json_response=True
)


@mcp.tool()
def lookup_business(query: str) -> dict:
    """Look up a Gainesville business by name, slug, or phone number.

    Returns the business profile including its live demo website URL, or
    {"found": false, "suggestions": [...]} with close matches to offer the
    caller ("did you mean ...?").
    """
    try:
        return find_business(query)
    except Exception as e:  # keep failures speakable, never raise
        return {"found": False, "suggestions": [], "error": _unavailable(e)}


@mcp.tool()
def get_pitch_info() -> dict:
    """The Florida Man Web Services sales cheat sheet: the offer and price,
    objection-handling lines, compliance rules you must follow on every
    call, and what to do instead of texting (this server cannot send SMS).
    """
    try:
        return get_pitch()
    except Exception as e:
        return {"error": _unavailable(e)}


@mcp.tool()
def get_call_history(business: str) -> dict:
    """Past call-log entries for a business (by name, slug, or phone), oldest
    first — check before pitching so you know prior contact and outcomes."""
    try:
        hit = find_business(business)
        if not hit.get("found"):
            return {"found": False, "suggestions": hit.get("suggestions", [])}
        return {"found": True, "slug": hit["slug"], "calls": history_for(hit["slug"])}
    except Exception as e:
        return {"found": False, "suggestions": [], "error": _unavailable(e)}


@mcp.tool()
def log_call_outcome(
    business: str,
    outcome: str,
    notes: str,
    email: str = "",
    callback_time: str = "",
) -> dict:
    """Record how the call went. Call exactly once near the end of every
    call. Outcomes: interested, wants_email, callback_requested, sent_sms,
    not_interested, do_not_call, wrong_number, voicemail, other. Use
    do_not_call whenever the person asks not to be contacted again."""
    try:
        hit = find_business(business)
        if not hit.get("found"):
            return {
                "logged": False,
                "error": f"unknown business {business!r}",
                "suggestions": hit.get("suggestions", []),
            }
        import businesses
        return append_outcome(
            businesses.by_slug(hit["slug"]), outcome, notes, email, callback_time
        )
    except Exception as e:
        return {"logged": False, "error": _unavailable(e)}


def _unavailable(e: Exception) -> str:
    return (
        f"data unavailable ({e.__class__.__name__}) — apologize and offer "
        "the owner's callback number from get_pitch_info"
    )


class BearerAuth:
    """401 everything except /health unless Authorization: Bearer matches."""

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] != "/health":
            auth = next(
                (v.decode() for k, v in scope.get("headers", [])
                 if k == b"authorization"),
                "",
            )
            if not self.token or auth != f"Bearer {self.token}":
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
    uvicorn.run(
        build_app(), host="0.0.0.0", port=int(os.getenv("PORT", "8036"))
    )


if __name__ == "__main__":
    main()
