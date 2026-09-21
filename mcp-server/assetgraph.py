"""Asset-graph closure audit + generation bump for generated-sites clones.

Background: floridamanweb assets are served with `cache-control: immutable`
and there is no Cloudflare purge access for the zone. Any byte change under an
already-exposed filename is permanently poisoned at the edge, which historically
produced cross-generation import cascades (two React copies, React error #321,
hydration removeChild failures, blank pages).

Contract enforced here:
- Every JS file a clone references (HTML script/link tags, TanStack Start
  $_TSR payload preloads/scripts, module import specifiers, __vite__mapDeps
  arrays) must exist inside the same clone's assets directory.
- No reference may escape the clone namespace (absolute /assets/... or ../).
- When chunk CONTENT changes, the whole JS graph must be promoted to fresh
  never-exposed filenames in one atomic pass (bump_generation).

The audit is fail-closed at PR time (open_site_update_pr) and in CI
(scripts/audit_asset_graphs.py). The bump is available for agent waves that
edit bundle content.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_HTML_JS_RE = re.compile(
    r'(?:src|href)="[^"]*?assets/([A-Za-z0-9_][A-Za-z0-9_.\-]*\.js)"'
)
_PAYLOAD_JS_RE = re.compile(
    r'["`](?:[A-Za-z0-9_.\-]+/)?assets/([A-Za-z0-9_][A-Za-z0-9_.\-]*\.js)["`]'
)
_JS_IMPORT_RES = (
    re.compile(r'import\(["`](\./)?([A-Za-z0-9_][A-Za-z0-9_.\-]*\.js)["`]\)'),
    re.compile(r'from["`](\./)?([A-Za-z0-9_][A-Za-z0-9_.\-]*\.js)["`]'),
)
_ESCAPED_RE = re.compile(r'["`/](?:\.\./|/)(?:assets|__l5e)/')


def referenced_js(html_text: str, js_texts: dict[str, str]) -> set[str]:
    """Filenames (.js, no directory) referenced by the clone graph."""
    refs: set[str] = set()
    refs.update(_HTML_JS_RE.findall(html_text))
    refs.update(_PAYLOAD_JS_RE.findall(html_text))
    for text in js_texts.values():
        for rx in _JS_IMPORT_RES:
            refs.update(m.group(2) for m in rx.finditer(text))
    return refs


def audit_closure(
    html_text: str, stem_dir: Path, stem: str
) -> dict[str, Any]:
    """Fail-closed graph audit for one clone stem.

    Returns {"ok": bool, "missing": [...], "escaped": [...], "js_files": [...]}.
    """
    stem_dir = Path(stem_dir)
    js_files = sorted(p.name for p in stem_dir.glob("*.js")) if stem_dir.is_dir() else []
    js_texts = {name: (stem_dir / name).read_text(encoding="utf-8", errors="replace") for name in js_files}
    refs = referenced_js(html_text, js_texts)
    existing = set(js_files)
    missing = sorted(refs - existing)
    escaped = sorted(
        {
            hit
            for text in [html_text, *js_texts.values()]
            for hit in _ESCAPED_RE.findall(text)
        }
    )
    return {
        "ok": not missing and not escaped,
        "missing": missing,
        "escaped": escaped,
        "js_files": js_files,
    }


def bump_generation(
    stem: str, tree_root: Path, tag: str, html_name: str | None = None
) -> dict[str, Any]:
    """Promote the entire JS graph of one clone to fresh `<tag>-*` filenames.

    Renames every .js in generated-sites/<stem>/assets/ and patches every
    reference across the JS files and the stem HTML. One atomic pass; refuses
    to overwrite an existing target name.
    """
    tag = re.sub(r"[^A-Za-z0-9_\-]", "", str(tag))
    if not tag:
        return {"ok": False, "error": "tag is required"}
    stem_dir = Path(tree_root) / "generated-sites" / stem / "assets"
    if not stem_dir.is_dir():
        return {"ok": False, "error": f"no assets dir for {stem!r}"}
    html_path = Path(tree_root) / "generated-sites" / (html_name or f"{stem}.html")

    js_files = sorted(p for p in stem_dir.glob("*.js"))
    if not js_files:
        return {"ok": False, "error": f"no js files under {stem}/assets"}
    renames = {p.name: f"{tag}-{p.name}" for p in js_files}
    for old, new in renames.items():
        if (stem_dir / new).exists():
            return {"ok": False, "error": f"target name already exists: {new}"}

    targets = [p for p in js_files] + ([html_path] if html_path.is_file() else [])
    counts: dict[str, int] = {}
    for f in targets:
        text = f.read_text(encoding="utf-8", errors="replace")
        orig = text
        for old, new in renames.items():
            c = text.count(old)
            if c:
                counts[old] = counts.get(old, 0) + c
                text = text.replace(old, new)
        if text != orig:
            f.write_text(text, encoding="utf-8")
    for old, new in renames.items():
        (stem_dir / old).rename(stem_dir / new)

    return {
        "ok": True,
        "tag": tag,
        "renames": renames,
        "ref_counts": counts,
    }
