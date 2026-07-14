"""In-process dispatch from AI 411 voice tools to mcp-server stores (#51).

Imports knowledge / events / callers / broadcasts / lookup from REPO_ROOT/mcp-server
(plus voice-agent businesses for lookup). On import or execution failure, returns a
speakable stub so live calls never crash.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

import ai411

log = logging.getLogger("voice-agent.mcp_bridge")

_AGENT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _AGENT_DIR.parent
_MCP_DIR = _REPO_ROOT / "mcp-server"

_paths_ready = False
_import_error: str | None = None
_mods: dict[str, Any] = {}


def _ensure_import_paths() -> None:
    """Make mcp-server + voice-agent importable (mcp modules win on name clash)."""
    global _paths_ready
    if _paths_ready:
        return
    # voice-agent first (businesses, config), then mcp-server at front for
    # knowledge/events/callers/broadcasts/lookup.
    va = str(_AGENT_DIR)
    mcp = str(_MCP_DIR)
    if va not in sys.path:
        sys.path.insert(0, va)
    if mcp not in sys.path:
        sys.path.insert(0, mcp)
    elif sys.path[0] != mcp:
        # Prefer mcp-server for its own modules.
        try:
            sys.path.remove(mcp)
        except ValueError:
            pass
        sys.path.insert(0, mcp)
    _paths_ready = True


def _load_modules() -> str | None:
    """Lazy-import mcp-server modules. Returns error string or None on success."""
    global _import_error
    if _mods:
        return None
    if _import_error is not None and not _mods:
        # Retry after a previous failure (paths/env may have changed in tests).
        _import_error = None
    _ensure_import_paths()
    try:
        import broadcasts as broadcasts_mod
        import callers as callers_mod
        import events as events_mod
        import knowledge as knowledge_mod
        from lookup import find_business

        _mods["broadcasts"] = broadcasts_mod
        _mods["callers"] = callers_mod
        _mods["events"] = events_mod
        _mods["knowledge"] = knowledge_mod
        _mods["find_business"] = find_business
        return None
    except Exception as e:  # noqa: BLE001 — never crash the call path
        _import_error = f"{e.__class__.__name__}: {e}"
        log.warning("mcp-server AI 411 imports failed: %s", _import_error)
        return _import_error


def _json(result: Any) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(result, default=str, ensure_ascii=False)


def _phone(args: dict, caller_number: str) -> str:
    """Prefer explicit phone arg; fall back to call state number."""
    raw = args.get("phone") or caller_number or ""
    return str(raw).strip()


def _kind_to_category(args: dict) -> str:
    """Map list_recent_broadcasts kind/category args to broadcasts.list category."""
    if "category" in args and args.get("category") not in (None, ""):
        return str(args["category"]).strip().lower()
    kind = (args.get("kind") or "all").strip().lower()
    if kind in ("", "all"):
        return ""
    if kind == "event":
        return "event"
    if kind == "notice":
        # Store filters notices by notice category (tips/music/…); empty lists all.
        return ""
    return kind


def _dispatch(name: str, args: dict, *, caller_number: str) -> Any:
    err = _load_modules()
    if err:
        return ai411.stub_tool_result(name, args)

    knowledge = _mods["knowledge"]
    events = _mods["events"]
    callers = _mods["callers"]
    broadcasts = _mods["broadcasts"]
    find_business = _mods["find_business"]

    if name == "search_business_knowledge":
        return knowledge.search_business_knowledge(
            query=str(args.get("query") or ""),
            limit=int(args.get("limit") or 5),
        )

    if name == "get_business_snapshot":
        return knowledge.get_business_snapshot(str(args.get("slug") or ""))

    if name == "lookup_business":
        return find_business(str(args.get("query") or ""))

    if name == "search_events":
        tags = args.get("tags")
        free_only = args.get("free_only", False)
        if isinstance(free_only, str):
            free_only = free_only.strip().lower() in ("1", "true", "yes", "on")
        return events.search_events(
            query=str(args.get("query") or ""),
            when=str(args.get("when") or ""),
            tags=tags,
            free_only=bool(free_only),
            limit=int(args.get("limit") or 5),
        )

    if name == "get_event":
        eid = args.get("event_id") or args.get("id") or ""
        return events.get_event(str(eid))

    if name == "get_caller_profile":
        return callers.get_profile(_phone(args, caller_number))

    if name == "update_caller_profile":
        patch = args.get("patch")
        if patch is None:
            # Allow top-level field args as a shorthand patch.
            patch = {
                k: v
                for k, v in args.items()
                if k not in ("phone", "patch")
            }
        return callers.update_profile(_phone(args, caller_number), patch)

    if name == "forget_caller":
        return callers.forget_profile(_phone(args, caller_number))

    if name == "add_caller_note":
        note = args.get("note") or args.get("text") or ""
        return callers.add_note(_phone(args, caller_number), str(note))

    if name == "submit_event_broadcast":
        # TOOLS may use when/where/summary; MCP uses when_start/venue/text/phone.
        when_start = (
            args.get("when_start")
            or args.get("when")
            or ""
        )
        venue = args.get("venue") or args.get("where") or ""
        text = args.get("text") or args.get("summary") or ""
        phone = _phone(args, caller_number)
        if args.get("contact") and not phone:
            phone = str(args.get("contact") or "")
        return broadcasts.submit_event_broadcast(
            title=str(args.get("title") or ""),
            when_start=str(when_start),
            venue=str(venue),
            phone=phone,
            when_end=str(args.get("when_end") or ""),
            free=bool(args["free"]) if "free" in args else True,
            tags=args.get("tags"),
            url=str(args.get("url") or ""),
            text=str(text),
        )

    if name == "submit_notice_broadcast":
        text = args.get("text") or args.get("summary") or ""
        category = (
            args.get("category")
            or args.get("area")
            or "general"
        )
        category = str(category).strip().lower() or "general"
        # Notice categories are tips|music|food|traffic|general — freeform area
        # becomes general with area folded into text.
        known = getattr(broadcasts, "NOTICE_CATEGORIES", frozenset())
        if known and category not in known:
            area = category
            category = "general"
            if area and area not in str(text).lower():
                text = f"{text} ({area})".strip()
        return broadcasts.submit_notice_broadcast(
            text=str(text),
            category=category,
            phone=_phone(args, caller_number),
            expires_at=str(args.get("expires_at") or ""),
        )

    if name == "list_recent_broadcasts":
        return broadcasts.list_recent_broadcasts(
            category=_kind_to_category(args),
            limit=int(args.get("limit") or 5),
        )

    if name == "report_broadcast":
        return broadcasts.report_broadcast(
            broadcast_id=str(args.get("broadcast_id") or args.get("id") or ""),
            reason=str(args.get("reason") or ""),
            reporter_phone=_phone(args, caller_number),
        )

    if name == "delete_own_broadcast":
        return broadcasts.delete_own_broadcast(
            broadcast_id=str(args.get("broadcast_id") or args.get("id") or ""),
            phone=_phone(args, caller_number),
        )

    return ai411.stub_tool_result(name, args)


def run_ai411_tool(name: str, args: dict | None, *, caller_number: str = "") -> str:
    """Run an AI 411 tool; always returns a string safe to feed back to the LLM."""
    args = dict(args or {})
    try:
        result = _dispatch(name, args, caller_number=caller_number or "")
        return _json(result)
    except Exception as e:  # noqa: BLE001
        log.warning("AI 411 tool %s raised: %s", name, e, exc_info=True)
        return ai411.stub_tool_result(name, args)


def reset_for_tests() -> None:
    """Drop cached imports (tests that change env / sys.path)."""
    global _import_error, _paths_ready
    _mods.clear()
    _import_error = None
    # Keep paths; re-import is enough.
