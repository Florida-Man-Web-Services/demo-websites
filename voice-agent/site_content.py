"""What's on a business's demo website, as plain text for the call prompt.

The agent always knows *which* site matters (the call resolves to one slug),
and a whole page is only a few KB of text — so site knowledge is a lookup
injected at call start, not a retrieval system. Sources, in order:

1. ``<slug>.facts.json`` beside the page — optional curated/crawled facts
   (deals, events, hours). A refresh job can rewrite these without touching
   the page HTML, so facts can change while the page's hash URL stays frozen.
2. ``<slug>.html`` — the generated page itself, stripped to visible text.
"""

import html as html_lib
import json
import re

import config

# Keeps the prompt bounded on pathological pages; real pages strip to 2-4 KB.
MAX_CHARS = 6000

_INVISIBLE = re.compile(r"<(script|style|noscript)\b[^>]*>.*?</\1\s*>", re.S | re.I)
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _strip_html(raw: str) -> str:
    text = _INVISIBLE.sub(" ", raw)
    text = _TAGS.sub(" ", text)
    text = html_lib.unescape(text)
    return _WS.sub(" ", text).strip()


def _render_facts(data) -> str:
    """Flatten a facts dict to speakable "key: value" lines."""
    if isinstance(data, str):
        return data.strip()
    lines = []
    for key, value in data.items():
        if isinstance(value, dict):
            value = "; ".join(f"{k}: {v}" for k, v in value.items())
        elif isinstance(value, (list, tuple)):
            value = "; ".join(str(v) for v in value)
        lines.append(f"{key.replace('_', ' ')}: {value}")
    return "\n".join(lines)


def site_text(slug: str) -> str | None:
    """The site's content for prompt injection, or None if nothing is on disk."""
    facts_path = config.GENERATED_SITES_DIR / f"{slug}.facts.json"
    if facts_path.is_file():
        try:
            text = _render_facts(json.loads(facts_path.read_text(encoding="utf-8")))
            if text:
                return text[:MAX_CHARS]
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            pass  # bad sidecar never breaks a live call — fall through to HTML

    page_path = config.GENERATED_SITES_DIR / f"{slug}.html"
    if not page_path.is_file():
        return None
    text = _strip_html(page_path.read_text(encoding="utf-8", errors="replace"))
    return text[:MAX_CHARS] or None
