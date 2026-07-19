"""Dispatch AI 411 voice tools to mcp-server stores (#51).

Backends (config.MCP_MODE):
  inproc (default) — import knowledge/events/callers/broadcasts/lookup from
                     REPO_ROOT/mcp-server (current behavior).
  http             — Streamable HTTP tools/call against MCP_URL (+ bearer).
  auto             — try inproc import; if it fails and MCP_URL is set, use http.

On any failure, returns a speakable stub so live calls never crash.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx

import ai411
import owner_updates
import config

log = logging.getLogger("voice-agent.mcp_bridge")

_AGENT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _AGENT_DIR.parent
_MCP_DIR = _REPO_ROOT / "mcp-server"

_paths_ready = False
_import_error: str | None = None
_mods: dict[str, Any] = {}

_owner_mods: dict[str, Any] = {}
_owner_import_error: str | None = None

# Resolved backend for this process: "inproc" | "http" | None (not yet chosen).
_backend: str | None = None
_backend_note: str | None = None

# MCP protocol version accepted by current FastMCP servers.
_MCP_PROTOCOL = "2024-11-05"
_HTTP_TIMEOUT = 20.0


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


def _resolve_backend() -> str:
    """Pick inproc vs http once (or again after reset_for_tests)."""
    global _backend, _backend_note
    if _backend is not None:
        return _backend

    mode = (getattr(config, "MCP_MODE", None) or "inproc").strip().lower()
    url = (getattr(config, "MCP_URL", None) or "").strip()

    if mode == "http":
        _backend = "http"
        _backend_note = "MCP_MODE=http"
        return _backend

    if mode == "auto":
        err = _load_modules()
        if err is None:
            _backend = "inproc"
            _backend_note = "MCP_MODE=auto (inproc ok)"
            return _backend
        if url:
            _backend = "http"
            _backend_note = f"MCP_MODE=auto fell back to http ({err})"
            log.info("mcp_bridge auto → http: %s", err)
            return _backend
        _backend = "inproc"
        _backend_note = f"MCP_MODE=auto inproc failed, no MCP_URL ({err})"
        return _backend

    # default / inproc / unknown
    if mode not in ("inproc", ""):
        log.warning("Unknown MCP_MODE %r; using inproc", mode)
    _backend = "inproc"
    _backend_note = "MCP_MODE=inproc"
    return _backend


def _map_tool_call(name: str, args: dict, *, caller_number: str) -> tuple[str, dict]:
    """Map voice tool name+args to MCP tool name + keyword arguments.

    Shared by inproc (after import) and http so signatures stay aligned with
    mcp-server/server.py tool defs.
    """
    args = dict(args or {})

    if name == "search_business_knowledge":
        return name, {
            "query": str(args.get("query") or ""),
            "limit": int(args.get("limit") or 5),
        }

    if name == "get_business_snapshot":
        return name, {"slug": str(args.get("slug") or "")}

    if name == "lookup_business":
        return name, {"query": str(args.get("query") or "")}

    if name == "search_events":
        tags = args.get("tags")
        free_only = args.get("free_only", False)
        if isinstance(free_only, str):
            free_only = free_only.strip().lower() in ("1", "true", "yes", "on")
        out: dict[str, Any] = {
            "query": str(args.get("query") or ""),
            "when": str(args.get("when") or ""),
            "free_only": bool(free_only),
            "limit": int(args.get("limit") or 5),
        }
        if tags is not None:
            out["tags"] = tags
        return name, out

    if name == "get_event":
        eid = args.get("event_id") or args.get("id") or ""
        return name, {"event_id": str(eid)}

    if name == "get_caller_profile":
        return name, {"phone": _phone(args, caller_number)}

    if name == "update_caller_profile":
        patch = args.get("patch")
        if patch is None:
            patch = {k: v for k, v in args.items() if k not in ("phone", "patch")}
        return name, {"phone": _phone(args, caller_number), "patch": patch}

    if name == "forget_caller":
        return name, {"phone": _phone(args, caller_number)}

    if name == "add_caller_note":
        note = args.get("note") or args.get("text") or ""
        return name, {"phone": _phone(args, caller_number), "note": str(note)}

    if name == "submit_event_broadcast":
        when_start = args.get("when_start") or args.get("when") or ""
        venue = args.get("venue") or args.get("where") or ""
        text = args.get("text") or args.get("summary") or ""
        phone = _phone(args, caller_number)
        if args.get("contact") and not phone:
            phone = str(args.get("contact") or "")
        out = {
            "title": str(args.get("title") or ""),
            "when_start": str(when_start),
            "venue": str(venue),
            "phone": phone,
            "when_end": str(args.get("when_end") or ""),
            "free": bool(args["free"]) if "free" in args else True,
            "url": str(args.get("url") or ""),
            "text": str(text),
        }
        if args.get("tags") is not None:
            out["tags"] = args.get("tags")
        return name, out

    if name == "submit_notice_broadcast":
        text = args.get("text") or args.get("summary") or ""
        category = args.get("category") or args.get("area") or "general"
        category = str(category).strip().lower() or "general"
        # Notice categories are tips|music|food|traffic|general — freeform area
        # becomes general with area folded into text (same as inproc).
        known = frozenset({"tips", "music", "food", "traffic", "general"})
        if _mods.get("broadcasts"):
            known = getattr(_mods["broadcasts"], "NOTICE_CATEGORIES", known) or known
        if category not in known:
            area = category
            category = "general"
            if area and area not in str(text).lower():
                text = f"{text} ({area})".strip()
        return name, {
            "text": str(text),
            "category": category,
            "phone": _phone(args, caller_number),
            "expires_at": str(args.get("expires_at") or ""),
        }

    if name == "list_recent_broadcasts":
        return name, {
            "category": _kind_to_category(args),
            "limit": int(args.get("limit") or 5),
        }

    if name == "report_broadcast":
        return name, {
            "broadcast_id": str(args.get("broadcast_id") or args.get("id") or ""),
            "reason": str(args.get("reason") or ""),
            "reporter_phone": _phone(args, caller_number),
        }

    if name == "delete_own_broadcast":
        return name, {
            "broadcast_id": str(args.get("broadcast_id") or args.get("id") or ""),
            "phone": _phone(args, caller_number),
        }

    return name, dict(args)


def _dispatch_inproc(name: str, args: dict, *, caller_number: str) -> Any:
    err = _load_modules()
    if err:
        return ai411.stub_tool_result(name, args)

    mcp_name, mapped = _map_tool_call(name, args, caller_number=caller_number)

    knowledge = _mods["knowledge"]
    events = _mods["events"]
    callers = _mods["callers"]
    broadcasts = _mods["broadcasts"]
    find_business = _mods["find_business"]

    if mcp_name == "search_business_knowledge":
        return knowledge.search_business_knowledge(**mapped)

    if mcp_name == "get_business_snapshot":
        return knowledge.get_business_snapshot(mapped["slug"])

    if mcp_name == "lookup_business":
        return find_business(mapped["query"])

    if mcp_name == "search_events":
        return events.search_events(**mapped)

    if mcp_name == "get_event":
        return events.get_event(mapped["event_id"])

    if mcp_name == "get_caller_profile":
        return callers.get_profile(mapped["phone"])

    if mcp_name == "update_caller_profile":
        return callers.update_profile(mapped["phone"], mapped.get("patch"))

    if mcp_name == "forget_caller":
        return callers.forget_profile(mapped["phone"])

    if mcp_name == "add_caller_note":
        return callers.add_note(mapped["phone"], mapped["note"])

    if mcp_name == "submit_event_broadcast":
        return broadcasts.submit_event_broadcast(**mapped)

    if mcp_name == "submit_notice_broadcast":
        return broadcasts.submit_notice_broadcast(**mapped)

    if mcp_name == "list_recent_broadcasts":
        return broadcasts.list_recent_broadcasts(**mapped)

    if mcp_name == "report_broadcast":
        return broadcasts.report_broadcast(**mapped)

    if mcp_name == "delete_own_broadcast":
        return broadcasts.delete_own_broadcast(**mapped)

    return ai411.stub_tool_result(name, args)


def _unauthorized_message(name: str) -> dict:
    return {
        "ok": False,
        "error": (
            f"MCP HTTP backend is not authorized for tool {name}. "
            "Set MCP_AUTH_TOKEN (Bearer) for the remote server. "
            "Apologize briefly; do not invent data."
        ),
        "unauthorized": True,
    }


def _http_config_error(name: str, detail: str) -> dict:
    return {
        "ok": False,
        "error": (
            f"MCP HTTP backend cannot run tool {name}: {detail}. "
            "Apologize briefly; do not invent data."
        ),
    }


def _parse_mcp_http_body(body: str, content_type: str) -> dict:
    """Parse JSON or single-event SSE body from Streamable HTTP."""
    text = (body or "").strip()
    if not text:
        return {}
    ct = (content_type or "").lower()
    if "text/event-stream" in ct or text.startswith("event:") or "data:" in text[:80]:
        # Collect last JSON data: line from SSE.
        data_lines: list[str] = []
        for line in text.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
            elif line.strip() == "" and data_lines:
                # end of one event — keep going; prefer last event
                pass
        if data_lines:
            # Prefer the last non-empty data payload (often tools/call result).
            for chunk in reversed(data_lines):
                if not chunk or chunk == "[DONE]":
                    continue
                try:
                    return json.loads(chunk)
                except json.JSONDecodeError:
                    continue
        return {"raw": text}
    return json.loads(text)


def _extract_tool_result(rpc: dict) -> Any:
    """Pull tool result content from a tools/call JSON-RPC response."""
    if not isinstance(rpc, dict):
        return rpc
    if rpc.get("error"):
        err = rpc["error"]
        if isinstance(err, dict):
            msg = err.get("message") or err.get("data") or err
            code = err.get("code")
            return {
                "ok": False,
                "error": f"MCP error{f' {code}' if code is not None else ''}: {msg}",
            }
        return {"ok": False, "error": f"MCP error: {err}"}

    result = rpc.get("result")
    if result is None:
        return rpc

    # MCP CallToolResult: { content: [{type:text, text:...}], isError?, structuredContent? }
    if isinstance(result, dict):
        if "structuredContent" in result and result["structuredContent"] is not None:
            return result["structuredContent"]
        content = result.get("content")
        if isinstance(content, list) and content:
            texts = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and "text" in block:
                    texts.append(block["text"])
            if len(texts) == 1:
                t = texts[0]
                try:
                    return json.loads(t)
                except (json.JSONDecodeError, TypeError):
                    return t
            if texts:
                return "\n".join(texts)
        # Maybe the server returned the dict directly (some proxies).
        if "content" not in result and "isError" not in result:
            return result
        if result.get("isError"):
            return {
                "ok": False,
                "error": _json(result.get("content") or result),
            }
    return result


def _http_post_rpc(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    method: str,
    params: dict | None,
    *,
    req_id: Any,
    session_id: str | None,
) -> tuple[dict, str | None]:
    """POST one JSON-RPC message; return (parsed body, session id header)."""
    hdrs = dict(headers)
    if session_id:
        hdrs["Mcp-Session-Id"] = session_id
    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
    }
    if params is not None:
        payload["params"] = params

    resp = client.post(url, headers=hdrs, json=payload)
    new_sid = resp.headers.get("mcp-session-id") or session_id

    if resp.status_code == 401:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": 401, "message": "unauthorized"},
        }, new_sid

    if resp.status_code >= 400:
        snippet = (resp.text or "")[:300]
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": resp.status_code,
                "message": f"HTTP {resp.status_code}: {snippet}",
            },
        }, new_sid

    try:
        body = _parse_mcp_http_body(resp.text, resp.headers.get("content-type", ""))
    except Exception as e:  # noqa: BLE001
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32700, "message": f"parse error: {e}"},
        }, new_sid
    return body, new_sid


def _http_notify(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    method: str,
    params: dict | None,
    session_id: str | None,
) -> None:
    hdrs = dict(headers)
    if session_id:
        hdrs["Mcp-Session-Id"] = session_id
    payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        payload["params"] = params
    # Notifications may return 202/204/200; ignore body.
    client.post(url, headers=hdrs, json=payload)


def call_mcp_tool_http(
    name: str,
    arguments: dict,
    *,
    url: str | None = None,
    token: str | None = None,
    client: httpx.Client | None = None,
) -> Any:
    """Call a remote MCP tool via Streamable HTTP (initialize + tools/call).

    Prefer real MCP JSON-RPC against FastMCP (json_response / streamable HTTP).
    ``client`` is injectable for tests.
    """
    url = (url if url is not None else getattr(config, "MCP_URL", "") or "").strip()
    token = (
        token if token is not None else getattr(config, "MCP_AUTH_TOKEN", "") or ""
    ).strip()

    if not url:
        return _http_config_error(name, "MCP_URL is not set")
    if not token:
        return _unauthorized_message(name)

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}",
    }

    owns_client = client is None
    http = client if client is not None else httpx.Client(timeout=_HTTP_TIMEOUT)

    try:
        session_id: str | None = None
        init_body, session_id = _http_post_rpc(
            http,
            url,
            headers,
            "initialize",
            {
                "protocolVersion": _MCP_PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "voice-agent-mcp-bridge", "version": "0.1"},
            },
            req_id=1,
            session_id=None,
        )
        if init_body.get("error"):
            err = init_body["error"]
            if isinstance(err, dict) and err.get("code") == 401:
                return _unauthorized_message(name)
            return _extract_tool_result(init_body)

        try:
            _http_notify(
                http,
                url,
                headers,
                "notifications/initialized",
                {},
                session_id,
            )
        except Exception as e:  # noqa: BLE001
            log.debug("notifications/initialized failed (continuing): %s", e)

        call_body, _ = _http_post_rpc(
            http,
            url,
            headers,
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            req_id=str(uuid.uuid4()),
            session_id=session_id,
        )
        if call_body.get("error"):
            err = call_body["error"]
            if isinstance(err, dict) and err.get("code") == 401:
                return _unauthorized_message(name)
        return _extract_tool_result(call_body)
    except httpx.HTTPError as e:
        log.warning("MCP HTTP transport error for %s: %s", name, e)
        return {
            "ok": False,
            "error": (
                f"MCP HTTP transport error for {name} ({e.__class__.__name__}). "
                "Apologize briefly; do not invent data."
            ),
        }
    finally:
        if owns_client:
            http.close()


def _dispatch_http(name: str, args: dict, *, caller_number: str) -> Any:
    mcp_name, mapped = _map_tool_call(name, args, caller_number=caller_number)
    # Unknown tools still get a remote call attempt only if they map; stubs for
    # names we deliberately do not bridge stay local.
    known = {
        "search_business_knowledge",
        "get_business_snapshot",
        "lookup_business",
        "search_events",
        "get_event",
        "get_caller_profile",
        "update_caller_profile",
        "forget_caller",
        "add_caller_note",
        "submit_event_broadcast",
        "submit_notice_broadcast",
        "list_recent_broadcasts",
        "report_broadcast",
        "delete_own_broadcast",
    }
    if mcp_name not in known:
        return ai411.stub_tool_result(name, args)
    return call_mcp_tool_http(mcp_name, mapped)


def _dispatch(name: str, args: dict, *, caller_number: str) -> Any:
    backend = _resolve_backend()
    if backend == "http":
        return _dispatch_http(name, args, caller_number=caller_number)
    return _dispatch_inproc(name, args, caller_number=caller_number)


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
    """Drop cached imports and backend choice (tests that change env / sys.path)."""
    global _import_error, _backend, _backend_note, _owner_import_error
    _mods.clear()
    _owner_mods.clear()
    _import_error = None
    _owner_import_error = None
    _backend = None
    _backend_note = None
    # Keep paths; re-import is enough.



def _dispatch_owner(
    name: str,
    args: dict,
    *,
    caller_number: str,
    call_sid: str = "",
) -> Any:
    err = _load_owner_modules()
    if err:
        return owner_updates.stub_tool_result(name, args)

    cr = _owner_mods["changerequests"]
    find_business = _owner_mods["find_business"]

    if name == "lookup_business":
        query = args.get("query") or args.get("phone") or caller_number or ""
        return find_business(str(query))

    if name == "get_site_outline":
        slug = args.get("slug") or args.get("business_slug") or ""
        return cr.get_site_outline(str(slug))

    if name == "create_change_request":
        conf = args.get("confirmation_spoken", True)
        if isinstance(conf, str):
            conf = conf.strip().lower() in ("1", "true", "yes", "on")
        return cr.create_change_request(
            business_slug=str(args.get("business_slug") or args.get("slug") or ""),
            summary=str(args.get("summary") or ""),
            items=_parse_items(args.get("items")),
            caller_phone=_phone(args, caller_number),
            source=str(args.get("source") or "voice"),
            confirmation_spoken=bool(conf),
            priority=str(args.get("priority") or "normal"),
            call_sid=str(args.get("call_sid") or call_sid or ""),
            transcript_ref=str(args.get("transcript_ref") or ""),
        )

    if name == "list_open_change_requests":
        slug = args.get("slug") or args.get("business_slug")
        if slug is not None:
            slug = str(slug).strip() or None
        return cr.list_open_change_requests(slug=slug)

    if name == "cancel_change_request":
        rid = args.get("request_id") or args.get("id") or ""
        return cr.cancel_change_request(str(rid))

    if name == "apply_change_request":
        rid = args.get("request_id") or args.get("id") or ""
        return cr.apply_change_request(str(rid))

    if name == "get_change_request":
        rid = args.get("request_id") or args.get("id") or ""
        return cr.get_change_request(str(rid))

    return owner_updates.stub_tool_result(name, args)


def _load_owner_modules() -> str | None:
    """Lazy-import changerequests + lookup for owner_updates mode."""
    global _owner_import_error
    if _owner_mods:
        return None
    if _owner_import_error is not None and not _owner_mods:
        _owner_import_error = None
    _ensure_import_paths()
    try:
        import changerequests as changerequests_mod
        from lookup import find_business

        _owner_mods["changerequests"] = changerequests_mod
        _owner_mods["find_business"] = find_business
        return None
    except Exception as e:  # noqa: BLE001
        _owner_import_error = f"{e.__class__.__name__}: {e}"
        log.warning("mcp-server owner_updates imports failed: %s", _owner_import_error)
        return _owner_import_error


def _parse_items(items: Any) -> Any:
    """Accept list or JSON string for create_change_request items."""
    if items is None:
        return None
    if isinstance(items, str):
        s = items.strip()
        if not s:
            return []
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            # Let changerequests._normalize_items report the error.
            return items
    return items


def run_owner_updates_tool(
    name: str,
    args: dict | None,
    *,
    caller_number: str = "",
    call_sid: str = "",
) -> str:
    """Run an owner_updates tool; always returns a string safe for the LLM."""
    args = dict(args or {})
    try:
        result = _dispatch_owner(
            name,
            args,
            caller_number=caller_number or "",
            call_sid=call_sid or "",
        )
        return _json(result)
    except Exception as e:  # noqa: BLE001
        log.warning("owner_updates tool %s raised: %s", name, e, exc_info=True)
        return owner_updates.stub_tool_result(name, args)
