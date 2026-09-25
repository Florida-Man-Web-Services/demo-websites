"""Oxford Dictionaries API proxy (key-safe, budget-protected).

Keys live ONLY in voice-agent env (OXFORD_APP_ID / OXFORD_APP_KEY) — never in
client HTML. The proxy 503s while keys are absent, so customer pages can ship
before enablement. In-memory TTL cache protects the call budget (the sandbox
tier has a hard 500-call cap): one upstream call-set per (word, lang) per hour,
bounded cache size.

The lean payload keeps every field the connected plan actually returns:
pronunciations, part of speech, style (registers), usage notes, domains,
regions, examples, subsenses, etymology, phrases, plus best-effort thesaurus,
sentences, and inflections when those endpoints answer.
"""

from __future__ import annotations

import html
import logging
import os
import re
import time
from typing import Any

import httpx

log = logging.getLogger("oxford")

OD_BASE = (os.getenv("OXFORD_BASE_URL") or "https://od-api.oxforddictionaries.com/api/v2").rstrip("/")
FALLBACK_BASE = (
    os.getenv("OXFORD_FALLBACK_URL") or "https://en.wiktionary.org/api/rest_v1/page/definition"
).rstrip("/")
_FALLBACK_UA = "fmws-voice-agent/1.0 (https://floridamanweb.online)"
DEFAULT_LANG = "en-gb"
_CACHE_TTL_S = 3600
_CACHE_MAX = 500
_MAX_SENSES = 8
_MAX_EXAMPLES = 4
_MAX_SUBS = 4

_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}

_JUNK_SENSE = re.compile(
    r"ISO 639|language code|letter-case form|abbreviation of",
    re.I,
)
_STYLE_HINT = re.compile(
    r"\b(informal|formal|slang|archaic|dated|literary|humorous|offensive|"
    r"vulgar|dialect|chiefly|figurative|literal|rare|obsolete|colloquial)\b",
    re.I,
)


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


def _strip_markup(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def _texts(items: Any) -> list[str]:
    out: list[str] = []
    for item in items or []:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        elif isinstance(item, dict):
            text = str(item.get("text") or item.get("id") or "").strip()
            if text:
                out.append(text)
    # preserve order, drop dupes
    seen: set[str] = set()
    uniq: list[str] = []
    for text in out:
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(text)
    return uniq


def _usable_sense(definition: str) -> bool:
    text = (definition or "").strip()
    return len(text) >= 8 and _JUNK_SENSE.search(text) is None


def _examples(sense: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for ex in sense.get("examples") or []:
        if isinstance(ex, str):
            text = _strip_markup(ex)
        elif isinstance(ex, dict):
            text = _strip_markup(str(ex.get("text") or ""))
        else:
            continue
        if text and text not in out:
            out.append(text)
        if len(out) >= _MAX_EXAMPLES:
            break
    return out


def _usage_notes(sense: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    for note in sense.get("notes") or []:
        if isinstance(note, str):
            text = note.strip()
        elif isinstance(note, dict):
            text = str(note.get("text") or "").strip()
        else:
            continue
        if text and text not in notes:
            notes.append(text)
    for marker in _texts(sense.get("crossReferenceMarkers")):
        if marker not in notes:
            notes.append(marker)
    return notes[:6]


def _project_sense(sense: dict[str, Any], part_of_speech: str = "") -> dict[str, Any] | None:
    defs = sense.get("definitions") or sense.get("shortDefinitions") or []
    definition = _strip_markup(defs[0]) if defs else ""
    subs = []
    for sub in sense.get("subsenses") or []:
        if not isinstance(sub, dict):
            continue
        projected = _project_sense(sub, part_of_speech)
        if projected:
            subs.append(projected)
        if len(subs) >= _MAX_SUBS:
            break
    if not _usable_sense(definition) and not subs:
        return None
    style = _texts(sense.get("registers"))
    # Parenthetical register at the start of a definition is style, not noise.
    lead = re.match(r"^\(([^)]{2,40})\)\s*", definition)
    if lead and _STYLE_HINT.search(lead.group(1)):
        label = lead.group(1).strip()
        if label.lower() not in {s.lower() for s in style}:
            style.insert(0, label)
    return {
        "definition": definition,
        "partOfSpeech": part_of_speech,
        "examples": _examples(sense),
        "example": (_examples(sense)[:1] or [""])[0],
        "style": style,
        "usage": _usage_notes(sense),
        "domains": _texts(sense.get("domains")) + _texts(sense.get("domainClasses")),
        "regions": _texts(sense.get("regions")),
        "topics": _texts(sense.get("semanticClasses")),
        "constructions": _texts(sense.get("constructions")),
        "subsenses": subs,
    }


def _phonetics(buckets: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for bucket in buckets:
        for item in bucket or []:
            if not isinstance(item, dict):
                continue
            spelling = str(item.get("phoneticSpelling") or "").strip()
            audio = str(item.get("audioFile") or "").strip()
            dialects = _texts(item.get("dialects"))
            key = spelling or audio
            if not key or key in seen:
                continue
            seen.add(key)
            out.append({"spelling": spelling, "audio": audio or None, "dialects": dialects})
    return out


def _phrases(lex: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for phrase in lex.get("phrases") or []:
        if not isinstance(phrase, dict):
            continue
        text = str(phrase.get("text") or "").strip()
        definition = ""
        for sense in phrase.get("senses") or []:
            if not isinstance(sense, dict):
                continue
            defs = sense.get("definitions") or sense.get("shortDefinitions") or []
            if defs:
                definition = _strip_markup(defs[0])
                break
        if text:
            out.append({"text": text, "definition": definition})
        if len(out) >= 6:
            break
    return out


def _empty(word: str | None, source: str) -> dict[str, Any]:
    return {
        "word": word,
        "phonetic": None,
        "phonetics": [],
        "etymology": [],
        "entries": [],
        "senses": [],
        "phrases": [],
        "synonyms": [],
        "antonyms": [],
        "sentences": [],
        "inflections": [],
        "source": source,
    }


def _lean_entry(payload: dict[str, Any]) -> dict[str, Any]:
    """Project an Oxford entries payload without dropping style or usage."""
    out = _empty(None, "oxford")
    for result in payload.get("results", [])[:1]:
        out["word"] = result.get("word")
        for lex in (result.get("lexicalEntries") or [])[:6]:
            if not isinstance(lex, dict):
                continue
            category = lex.get("lexicalCategory") or {}
            pos = str(category.get("text") or category.get("id") or "").strip()
            entry_senses: list[dict[str, Any]] = []
            etym: list[str] = []
            phon_buckets = [lex.get("pronunciations")]
            for entry in lex.get("entries", [])[:2]:
                if not isinstance(entry, dict):
                    continue
                phon_buckets.append(entry.get("pronunciations"))
                for item in entry.get("etymologies") or []:
                    text = _strip_markup(str(item))
                    if text and text not in etym:
                        etym.append(text)
                for sense in entry.get("senses", [])[:_MAX_SENSES]:
                    if not isinstance(sense, dict):
                        continue
                    projected = _project_sense(sense, pos)
                    if projected:
                        entry_senses.append(projected)
            phonetics = _phonetics(phon_buckets)
            if phonetics and not out["phonetics"]:
                out["phonetics"] = phonetics
                out["phonetic"] = phonetics[0]["spelling"] or None
            if etym and not out["etymology"]:
                out["etymology"] = etym[:2]
            phrases = _phrases(lex)
            if phrases:
                out["phrases"].extend(phrases)
            if entry_senses:
                out["entries"].append({"partOfSpeech": pos, "senses": entry_senses})
                out["senses"].extend(entry_senses)
    out["phrases"] = out["phrases"][:8]
    out["senses"] = out["senses"][:_MAX_SENSES]
    return out


def _wiki_style(raw_html: str, definition: str) -> list[str]:
    labels = [
        _strip_markup(chunk)
        for chunk in re.findall(
            r'class="(?:usage-label-sense|ib-content|qualifier-content)"[^>]*>(.*?)</span>',
            raw_html or "",
            flags=re.S,
        )
    ]
    labels = [label for label in labels if label]
    lead = re.match(r"^\(([^)]{2,40})\)\s*", definition or "")
    if lead and _STYLE_HINT.search(lead.group(1)):
        labels.insert(0, lead.group(1).strip())
    return _texts(labels)


def _lean_fallback(payload: Any, word: str) -> dict[str, Any] | None:
    """Project Wiktionary REST JSON, keeping part of speech, style, and examples."""
    entries = payload.get("en") if isinstance(payload, dict) else None
    if not isinstance(entries, list) or not entries:
        return None
    out = _empty(word, "wiktionary")
    ranked = sorted(
        [lex for lex in entries if isinstance(lex, dict)][:8],
        key=lambda lex: 0
        if str(lex.get("partOfSpeech") or "").lower()
        in {"noun", "verb", "adjective", "adverb"}
        else 1,
    )
    for lex in ranked:
        pos = str(lex.get("partOfSpeech") or "").strip()
        entry_senses: list[dict[str, Any]] = []
        for raw in (lex.get("definitions") or [])[:_MAX_SENSES]:
            if not isinstance(raw, dict):
                continue
            raw_html = str(raw.get("definition") or "")
            definition = _strip_markup(raw_html)
            if not _usable_sense(definition):
                continue
            examples = []
            for ex in (raw.get("parsedExamples") or raw.get("examples") or [])[:_MAX_EXAMPLES]:
                if isinstance(ex, str):
                    text = _strip_markup(ex)
                elif isinstance(ex, dict):
                    text = _strip_markup(str(ex.get("example") or ex.get("text") or ""))
                else:
                    continue
                if text:
                    examples.append(text)
            entry_senses.append(
                {
                    "definition": definition,
                    "partOfSpeech": pos,
                    "examples": examples,
                    "example": examples[0] if examples else "",
                    "style": _wiki_style(raw_html, definition),
                    "usage": [],
                    "domains": [],
                    "regions": [],
                    "topics": [],
                    "constructions": [],
                    "subsenses": [],
                }
            )
        if entry_senses:
            out["entries"].append({"partOfSpeech": pos, "senses": entry_senses})
            out["senses"].extend(entry_senses)
        if len(out["senses"]) >= _MAX_SENSES:
            break
    out["senses"] = out["senses"][:_MAX_SENSES]
    if not out["senses"]:
        return None
    return out


def _optional_json(url: str, headers: dict[str, str]) -> dict[str, Any] | None:
    try:
        resp = httpx.get(url, headers=headers, timeout=8.0)
    except httpx.HTTPError as e:
        log.info("oxford extra %s failed: %s", url.rsplit("/", 2)[-2], e.__class__.__name__)
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _merge_extras(lean: dict[str, Any], word: str, lang: str, headers: dict[str, str]) -> None:
    """Best-effort thesaurus, sentences, and inflections. Missing endpoints are ignored."""
    short = "en" if lang.startswith("en") else lang
    thesaurus = _optional_json(f"{OD_BASE}/thesaurus/{short}/{word}", headers)
    sentences = _optional_json(f"{OD_BASE}/sentences/{short}/{word}", headers)
    inflections = _optional_json(f"{OD_BASE}/inflections/{lang}/{word}", headers)
    if thesaurus:
        syn: list[str] = []
        ant: list[str] = []
        for result in thesaurus.get("results") or []:
            for lex in result.get("lexicalEntries") or []:
                for entry in lex.get("entries") or []:
                    for sense in entry.get("senses") or []:
                        syn.extend(_texts(sense.get("synonyms")))
                        ant.extend(_texts(sense.get("antonyms")))
        lean["synonyms"] = _texts(syn)[:12]
        lean["antonyms"] = _texts(ant)[:8]
    if sentences:
        lines: list[str] = []
        for result in sentences.get("results") or []:
            for lex in result.get("lexicalEntries") or []:
                for sentence in lex.get("sentences") or []:
                    text = str((sentence or {}).get("text") or "").strip()
                    if text:
                        lines.append(text)
        lean["sentences"] = lines[:6]
    if inflections:
        forms: list[str] = []
        for result in inflections.get("results") or []:
            for lex in result.get("lexicalEntries") or []:
                for form in lex.get("inflectionOf") or lex.get("grammaticalFeatures") or []:
                    forms.extend(_texts([form] if isinstance(form, dict) else []))
                for entry in lex.get("inflections") or []:
                    if isinstance(entry, dict):
                        forms.append(str(entry.get("inflectedForm") or entry.get("text") or "").strip())
        lean["inflections"] = [form for form in _texts(forms) if form][:12]


def _fallback_lookup(word: str) -> dict[str, Any] | None:
    try:
        resp = httpx.get(
            f"{FALLBACK_BASE}/{word}",
            timeout=10.0,
            headers={"User-Agent": _FALLBACK_UA},
        )
    except httpx.HTTPError as e:
        log.warning("fallback dictionary failed for %r: %s", word, e.__class__.__name__)
        return None
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        log.warning("fallback dictionary HTTP %s for %r", resp.status_code, word)
        return None
    try:
        return _lean_fallback(resp.json(), word)
    except ValueError:
        return None


def lookup(word: str, lang: str = DEFAULT_LANG) -> dict[str, Any]:
    """Return {"ok", "disabled"?, "cached"?, **entry or "error"}."""
    word = (word or "").strip().lower()
    if not word or len(word) > 60 or not word.replace("-", "").replace(" ", "").replace("'", "").isalnum():
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
    headers = {"app_id": app_id, "app_key": app_key}

    try:
        resp = httpx.get(
            f"{OD_BASE}/entries/{lang}/{word}",
            headers=headers,
            params={"strictMatch": "false"},
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        log.warning("oxford lookup failed for %r: %s", word, e.__class__.__name__)
        lean = _fallback_lookup(word)
        if lean:
            _cache_put(key, lean)
            return {"ok": True, "cached": False, **lean}
        return {"ok": False, "error": "dictionary unavailable"}

    if resp.status_code == 404:
        lean = _fallback_lookup(word)
        if lean:
            _cache_put(key, lean)
            return {"ok": True, "cached": False, **lean}
        return {"ok": False, "error": "word not found"}
    if resp.status_code in (401, 403):
        log.error("oxford credentials rejected (HTTP %s)", resp.status_code)
        return {"ok": False, "error": "dictionary unavailable"}
    if resp.status_code == 429:
        log.warning("oxford quota hit")
        return {"ok": False, "error": "dictionary quota reached"}
    if resp.status_code >= 400:
        log.warning("oxford HTTP %s for %r: %.300s", resp.status_code, word, resp.text)
        return {"ok": False, "error": "dictionary unavailable"}

    try:
        lean = _lean_entry(resp.json())
    except ValueError:
        lean = _empty(word, "oxford")
    if not lean.get("senses"):
        fallback = _fallback_lookup(word)
        if fallback:
            lean = fallback
    else:
        _merge_extras(lean, word, lang, headers)
    if not lean.get("senses"):
        return {"ok": False, "error": "word not found"}
    _cache_put(key, lean)
    return {"ok": True, "cached": False, **lean}


def cache_stats() -> dict[str, int]:
    return {"entries": len(_cache), "ttl_s": _CACHE_TTL_S, "max": _CACHE_MAX}
