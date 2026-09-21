"""Oxford Dictionaries API proxy (key-safe, budget-protected).

Keys live ONLY in voice-agent env (OXFORD_APP_ID / OXFORD_APP_KEY) — never in
client HTML. The proxy 503s while keys are absent, so customer pages can ship
before enablement. In-memory TTL cache protects the call budget (the sandbox
tier has a hard 500-call cap): one upstream call per (word, lang) per hour,
bounded cache size.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

log = logging.getLogger("oxford")

OD_BASE = "https://od-api.oxforddictionaries.com/api/v2"
DEFAULT_LANG = "en-gb"
_CACHE_TTL_S = 3600
_CACHE_MAX = 500

_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}


def credentials() -> tuple[str, str]:
    return (
        (os.getenv("OXFORD_APP_ID") or "").strip(),
        (os.getenv("OXFORD_APP_KEY") or "").strip(),
    )


def enabled() -> bool:
    app_id, app_key = credentials()
    return bool(app_id and app_key)


def _cache_get(key: tuple[str, str]) -> dict[str, Any] | None:
    hit = _cache.get(key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL_S:
        return hit[1]
    if hit:
        _cache.pop(key, None)
    return None


def _cache_put(key: tuple[str, str], value: dict[str, Any]) -> None:
    if len(_cache) >= _CACHE_MAX:
        oldest = min(_cache, key=lambda k: _cache[k][0])
        _cache.pop(oldest, None)
    _cache[key] = (time.time(), value)


def _lean_entry(payload: dict[str, Any]) -> dict[str, Any]:
    """Project the OD response down to what a lookup widget needs."""
    out: dict[str, Any] = {"word": None, "phonetic": None, "senses": []}
    for result in payload.get("results", [])[:1]:
        out["word"] = result.get("word")
        lexical = result.get("lexicalEntries") or []
        for lex in lexical[:2]:
            pron = ((lex.get("pronunciations") or [{}])[0].get("phoneticSpelling"))
            if pron and not out["phonetic"]:
                out["phonetic"] = pron
            for entry in lex.get("entries", [])[:1]:
                for sense in entry.get("senses", [])[:5]:
                    defs = sense.get("definitions") or []
                    out["senses"].append(
                        {
                            "definition": defs[0] if defs else "",
                            "example": (
                                (sense.get("examples") or [{}])[0].get("text", "")
                            ),
                        }
                    )
    return out


def lookup(word: str, lang: str = DEFAULT_LANG) -> dict[str, Any]:
    """Return {"ok", "disabled"?, "cached"?, **lean entry or "error"}."""
    word = (word or "").strip().lower()
    if not word or len(word) > 60 or not word.replace("-", "").replace(" ", "").isalnum():
        return {"ok": False, "error": "invalid word"}
    lang = (lang or DEFAULT_LANG).strip().lower()
    if lang not in ("en-gb", "en-us"):
        return {"ok": False, "error": "unsupported language"}

    key = (word, lang)
    cached = _cache_get(key)
    if cached is not None:
        return {"ok": True, "cached": True, **cached}

    app_id, app_key = credentials()
    if not (app_id and app_key):
        return {"ok": False, "disabled": True, "error": "dictionary not configured"}

    try:
        resp = httpx.get(
            f"{OD_BASE}/entries/{lang}/{word}",
            headers={"app_id": app_id, "app_key": app_key},
            params={"strictMatch": "false"},
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        log.warning("oxford lookup failed for %r: %s", word, e.__class__.__name__)
        return {"ok": False, "error": "dictionary unavailable"}

    if resp.status_code == 404:
        return {"ok": False, "error": "word not found"}
    if resp.status_code in (401, 403):
        log.error("oxford credentials rejected (HTTP %s)", resp.status_code)
        return {"ok": False, "error": "dictionary unavailable"}
    if resp.status_code == 429:
        log.warning("oxford quota hit")
        return {"ok": False, "error": "dictionary quota reached"}
    if resp.status_code >= 400:
        # Never echo upstream bodies (G05 discipline: bodies stay in logs).
        log.warning("oxford HTTP %s for %r: %.300s", resp.status_code, word, resp.text)
        return {"ok": False, "error": "dictionary unavailable"}

    lean = _lean_entry(resp.json())
    _cache_put(key, lean)
    return {"ok": True, "cached": False, **lean}


def cache_stats() -> dict[str, int]:
    return {"entries": len(_cache), "ttl_s": _CACHE_TTL_S, "max": _CACHE_MAX}
