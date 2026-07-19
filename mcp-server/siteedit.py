"""Surgical HTML transforms for structured ChangeRequest items (#52 slice C).

MVP types: hours, phone, address, copy.
Prefer exact before→after string replace; fall back to section/target heuristics.
Validates output still feeds html.parser without raising.
"""

from __future__ import annotations

import difflib
import html as html_lib
import re
from html.parser import HTMLParser
from typing import Any


SUPPORTED_APPLY_TYPES = frozenset({"hours", "phone", "address", "copy"})


class _ValidateParser(HTMLParser):
    """Feed-only parser used to confirm HTML is still parseable."""

    def error(self, message: str) -> None:  # pragma: no cover - py3.11 unused
        raise ValueError(message)


def validate_html(html: str) -> tuple[bool, str | None]:
    """Return (ok, error). html.parser is lenient; we only catch hard failures."""
    try:
        p = _ValidateParser(convert_charrefs=True)
        p.feed(html)
        p.close()
        return True, None
    except Exception as e:
        return False, f"{e.__class__.__name__}: {e}"


def _digits(s: str) -> str:
    return re.sub(r"\D+", "", s or "")


def _format_us_phone_display(raw: str) -> str:
    """Best-effort display form for 10/11 digit US numbers; else return stripped raw."""
    d = _digits(raw)
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    if len(d) == 10:
        return f"({d[0:3]}) {d[3:6]}-{d[6:10]}"
    return (raw or "").strip()


def _replace_first(haystack: str, needle: str, repl: str) -> tuple[str, bool]:
    if not needle:
        return haystack, False
    idx = haystack.find(needle)
    if idx < 0:
        return haystack, False
    return haystack[:idx] + repl + haystack[idx + len(needle) :], True


def _replace_all(haystack: str, needle: str, repl: str) -> tuple[str, int]:
    if not needle or needle not in haystack:
        return haystack, 0
    return haystack.replace(needle, repl), haystack.count(needle)


def _heading_block_pattern(heading_words: str) -> re.Pattern[str]:
    """Match an h1–h6 (or kicker/span) containing heading_words, then the next text-ish block."""
    esc = re.escape(heading_words)
    # Allow markup between words of the heading label.
    loose = re.sub(r"\\\s+", r"\\s+", esc)
    return re.compile(
        rf"(?is)(<(?:h[1-6]|span|div|p|strong|label)[^>]*>[^<]*{loose}[^<]*</(?:h[1-6]|span|div|p|strong|label)>)"
        rf"(\s*)"
        rf"((?:<(?:p|div|address|span|li)[^>]*>)(.*?)(</(?:p|div|address|span|li)>)"
        rf"|([^<\n][^\n]*))",
        re.DOTALL,
    )


def _apply_section_text_replace(
    html: str,
    section_labels: list[str],
    after: str,
) -> tuple[str, bool, str]:
    """Replace the first content block following a matching section label."""
    after_esc = html_lib.escape(after) if ("<" not in after and ">" not in after) else after
    for label in section_labels:
        pat = _heading_block_pattern(label)
        m = pat.search(html)
        if not m:
            continue
        head, ws = m.group(1), m.group(2)
        if m.group(3) and m.group(4) is not None and m.group(5):
            # Tagged block: keep open/close tags, replace inner text.
            open_close = m.group(3)
            # Rebuild: group3 is full tagged OR plain; prefer groups  from alt1
            full = m.group(0)
            # Safer: replace only the inner of first p/div/address after heading via a tighter re
            inner_pat = re.compile(
                rf"(?is)(<(?:h[1-6]|span|div|p|strong|label)[^>]*>[^<]*{re.escape(label)}[^<]*"
                rf"</(?:h[1-6]|span|div|p|strong|label)>\s*)"
                rf"(<(?:p|div|address|span|li)[^>]*>)(.*?)(</(?:p|div|address|span|li)>)",
                re.DOTALL,
            )
            m2 = inner_pat.search(html)
            if m2:
                new_html = html[: m2.start()] + m2.group(1) + m2.group(2) + after_esc + m2.group(4) + html[m2.end() :]
                return new_html, True, f"replaced section body after {label!r}"
            # Plain line after heading
            plain_pat = re.compile(
                rf"(?is)(<(?:h[1-6]|span|div|p|strong|label)[^>]*>[^<]*{re.escape(label)}[^<]*"
                rf"</(?:h[1-6]|span|div|p|strong|label)>\s*)([^<\n][^\n]*)",
            )
            m3 = plain_pat.search(html)
            if m3:
                new_html = html[: m3.start()] + m3.group(1) + after_esc + html[m3.end() :]
                return new_html, True, f"replaced plain line after {label!r}"
        else:
            # Fallback plain
            plain_pat = re.compile(
                rf"(?is)(<(?:h[1-6]|span|div|p|strong|label)[^>]*>[^<]*{re.escape(label)}[^<]*"
                rf"</(?:h[1-6]|span|div|p|strong|label)>\s*)([^<\n][^\n]*)",
            )
            m3 = plain_pat.search(html)
            if m3:
                new_html = html[: m3.start()] + m3.group(1) + after_esc + html[m3.end() :]
                return new_html, True, f"replaced plain line after {label!r}"
    return html, False, "section not found"


def _apply_phone(html: str, item: dict[str, Any]) -> tuple[str, bool, str]:
    after = str(item.get("after") or "").strip()
    before = item.get("before")
    if before is not None and str(before):
        new, ok = _replace_first(html, str(before), after)
        if ok:
            return new, True, "before→after phone replace"
        # try digit-normalized replace of before display
        bd = _digits(str(before))
        if bd and bd in _digits(html):
            # replace visible before and tel: variants
            html2, n = _replace_all(html, str(before), after)
            if n:
                return html2, True, f"replaced phone display ({n}x)"

    if not after:
        return html, False, "phone after value is empty"

    display = _format_us_phone_display(after)
    digs = _digits(after)
    if len(digs) == 11 and digs.startswith("1"):
        digs10 = digs[1:]
    else:
        digs10 = digs

    changed = False
    out = html
    notes: list[str] = []

    # Replace tel: href targets (digits with optional +1 / punctuation)
    if digs10 and len(digs10) >= 7:
        tel_target = f"tel:{digs10}"
        out2, n = re.subn(r"tel:\+?1?\d[\d.\-() ]*", tel_target, out, flags=re.I)
        if n:
            out = out2
            changed = True
            notes.append(f"tel: hrefs ({n})")

        # Common display forms of existing phones in page
        if len(digs10) == 10:
            phone_disp = re.compile(r"\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}")
            out3, n2 = phone_disp.subn(display, out)
            if n2:
                out = out3
                changed = True
                notes.append(f"display phones ({n2})")

    if before is not None and str(before) and str(before) in out:
        out, ok = _replace_first(out, str(before), after)
        if ok:
            changed = True
            notes.append("before string")

    if not changed:
        # Last resort: if target text exists, replace it
        target = str(item.get("target") or "").strip()
        if target and target in out:
            out, ok = _replace_first(out, target, after)
            if ok:
                return out, True, "replaced target text with phone"
        return html, False, "no phone pattern found to update"

    return out, True, "; ".join(notes) or "phone updated"


def _apply_hours(html: str, item: dict[str, Any]) -> tuple[str, bool, str]:
    after = str(item.get("after") or "")
    before = item.get("before")
    if before is not None and str(before):
        new, ok = _replace_first(html, str(before), after)
        if ok:
            return new, True, "before→after hours replace"

    labels = ["Hours", "hours", "Open", "Business Hours", "Opening Hours"]
    target = str(item.get("target") or "").strip()
    if target:
        labels = [target] + labels

    new, ok, note = _apply_section_text_replace(html, labels, after)
    if ok:
        return new, True, note

    # Pattern: "Hours: …" inline
    m = re.search(r"(?i)(Hours\s*:\s*)([^<\n]+)", html)
    if m and after:
        new_html = html[: m.start()] + m.group(1) + after + html[m.end() :]
        return new_html, True, "replaced Hours: inline"

    return html, False, "hours section not found"


def _apply_address(html: str, item: dict[str, Any]) -> tuple[str, bool, str]:
    after = str(item.get("after") or "")
    before = item.get("before")
    if before is not None and str(before):
        new, ok = _replace_first(html, str(before), after)
        if ok:
            return new, True, "before→after address replace"

    labels = ["Address", "Location", "Find us", "Visit us", "Our location"]
    target = str(item.get("target") or "").strip()
    if target:
        labels = [target] + labels

    new, ok, note = _apply_section_text_replace(html, labels, after)
    if ok:
        return new, True, note

    # <address>…</address>
    m = re.search(r"(?is)(<address[^>]*>)(.*?)(</address>)", html)
    if m and after:
        after_esc = html_lib.escape(after) if ("<" not in after) else after
        new_html = html[: m.start()] + m.group(1) + after_esc + m.group(3) + html[m.end() :]
        return new_html, True, "replaced <address> body"

    return html, False, "address section not found"


def _apply_copy(html: str, item: dict[str, Any]) -> tuple[str, bool, str]:
    after = str(item.get("after") or "")
    before = item.get("before")
    target = str(item.get("target") or "").strip()

    if before is not None and str(before):
        new, ok = _replace_first(html, str(before), after)
        if ok:
            return new, True, "before→after copy replace"
        return html, False, "before text not found for copy"

    if target and after:
        # Replace exact target string once
        if target in html:
            new, ok = _replace_first(html, target, after)
            if ok:
                return new, True, "replaced target string"

        # Heading whose text matches target → replace heading inner text
        hpat = re.compile(
            rf"(?is)(<(h[1-6])[^>]*>)(\s*)({re.escape(target)})(\s*)(</\2>)",
        )
        m = hpat.search(html)
        if m:
            new_html = (
                html[: m.start()]
                + m.group(1)
                + m.group(3)
                + after
                + m.group(5)
                + m.group(6)
                + html[m.end() :]
            )
            return new_html, True, f"replaced heading {target!r}"

        # Insert a paragraph after a heading matching target (if target is section name)
        insert_pat = re.compile(
            rf"(?is)(<(h[1-6])[^>]*>\s*{re.escape(target)}\s*</\2>)",
        )
        m2 = insert_pat.search(html)
        if m2:
            after_esc = html_lib.escape(after) if ("<" not in after) else after
            insertion = m2.group(1) + f"\n  <p>{after_esc}</p>"
            new_html = html[: m2.start()] + insertion + html[m2.end() :]
            return new_html, True, f"inserted paragraph after heading {target!r}"

    return html, False, "copy: need before text or a target found in the page"


def apply_item(html: str, item: dict[str, Any]) -> dict[str, Any]:
    """Apply one structured item. Returns {ok, html, type, note, error?}."""
    item_type = str(item.get("type") or "other").strip().lower()
    if item_type not in SUPPORTED_APPLY_TYPES:
        return {
            "ok": False,
            "html": html,
            "type": item_type,
            "note": "",
            "error": (
                f"item type {item_type!r} is not applied in this MVP "
                f"(supported: {', '.join(sorted(SUPPORTED_APPLY_TYPES))})"
            ),
            "skipped": True,
        }

    if item_type == "phone":
        new_html, ok, note = _apply_phone(html, item)
    elif item_type == "hours":
        new_html, ok, note = _apply_hours(html, item)
    elif item_type == "address":
        new_html, ok, note = _apply_address(html, item)
    else:
        new_html, ok, note = _apply_copy(html, item)

    if not ok:
        return {
            "ok": False,
            "html": html,
            "type": item_type,
            "note": note,
            "error": note or "apply failed",
        }

    valid, verr = validate_html(new_html)
    if not valid:
        return {
            "ok": False,
            "html": html,
            "type": item_type,
            "note": note,
            "error": f"edit produced unparseable HTML ({verr})",
        }

    return {
        "ok": True,
        "html": new_html,
        "type": item_type,
        "note": note,
    }


def apply_items(html: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply a list of items in order. Stops on first hard failure (not skip)."""
    current = html
    results: list[dict[str, Any]] = []
    applied = 0
    skipped = 0
    failed = 0

    for i, item in enumerate(items or []):
        if not isinstance(item, dict):
            results.append(
                {
                    "index": i,
                    "ok": False,
                    "error": f"items[{i}] must be an object",
                }
            )
            failed += 1
            return {
                "ok": False,
                "html": html,
                "applied": applied,
                "skipped": skipped,
                "failed": failed,
                "results": results,
                "error": f"items[{i}] must be an object",
            }

        r = apply_item(current, item)
        entry = {
            "index": i,
            "type": r.get("type"),
            "ok": r.get("ok"),
            "note": r.get("note") or "",
            "skipped": bool(r.get("skipped")),
        }
        if r.get("error"):
            entry["error"] = r["error"]
        results.append(entry)

        if r.get("skipped"):
            skipped += 1
            continue
        if not r.get("ok"):
            failed += 1
            return {
                "ok": False,
                "html": html,  # do not keep partial file writes at this layer
                "html_attempt": current,
                "applied": applied,
                "skipped": skipped,
                "failed": failed,
                "results": results,
                "error": r.get("error") or f"item {i} failed",
            }
        current = r["html"]
        applied += 1

    return {
        "ok": True,
        "html": current,
        "applied": applied,
        "skipped": skipped,
        "failed": failed,
        "results": results,
        "changed": current != html,
    }


def unified_diff(before: str, after: str, path: str = "site.html") -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3,
        )
    )


def summarize_change(before: str, after: str, max_chars: int = 400) -> dict[str, Any]:
    """Short speakable before/after summary for tool responses."""
    if before == after:
        return {
            "changed": False,
            "bytes_before": len(before.encode("utf-8")),
            "bytes_after": len(after.encode("utf-8")),
            "summary": "no content change",
        }
    # Strip tags for a rough text diff sample
    def _text(s: str) -> str:
        t = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", s)
        t = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", t)
        t = re.sub(r"<[^>]+>", " ", t)
        t = html_lib.unescape(re.sub(r"\s+", " ", t)).strip()
        return t

    tb, ta = _text(before), _text(after)
    # Find first differing window
    prefix = 0
    for a, b in zip(tb, ta):
        if a != b:
            break
        prefix += 1
    start = max(0, prefix - 40)
    snip_b = tb[start : start + max_chars]
    snip_a = ta[start : start + max_chars]
    return {
        "changed": True,
        "bytes_before": len(before.encode("utf-8")),
        "bytes_after": len(after.encode("utf-8")),
        "text_before_snip": snip_b,
        "text_after_snip": snip_a,
        "summary": "HTML content updated",
    }
