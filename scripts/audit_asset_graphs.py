#!/usr/bin/env python3
"""CI guard: every generated-sites clone's JS import graph must be closed.

Fails the build if any clone HTML or chunk references a JS file that does not
exist in the same clone's assets dir, or if a reference escapes the clone
namespace (absolute /assets/... or ../). This is the authoritative gate against
cache-poisoned cross-generation graphs (see mcp-server/assetgraph.py).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp-server"))

from assetgraph import audit_closure  # noqa: E402


def main() -> int:
    repo = Path(__file__).resolve().parent.parent
    gs = repo / "generated-sites"
    if not gs.is_dir():
        print("no generated-sites dir; nothing to audit")
        return 0

    failures: list[str] = []
    audited = 0
    for html in sorted(gs.glob("*.html")):
        stem = html.stem
        assets = gs / stem / "assets"
        if not assets.is_dir():
            continue  # static clone without a JS graph
        text = html.read_text(encoding="utf-8", errors="replace")
        result = audit_closure(text, assets, stem)
        audited += 1
        if not result["ok"]:
            failures.append(
                f"{html.name}: missing={result['missing']} escaped={result['escaped']}"
            )

    print(f"audited {audited} clone graph(s)")
    for f in failures:
        print(f"FAIL {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
