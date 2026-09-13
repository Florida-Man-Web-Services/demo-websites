#!/usr/bin/env python3
"""Fail if Nuxt entry.js preloads CSS that is not on disk."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "generated-sites/florida-man-bioscience-site/_nuxt"
NEEDLE = "index.2a86cd7c.css"


def missing_index_css() -> list[str]:
    on_disk = {p.name for p in ROOT.iterdir()}
    pat = re.compile(r"(index\.[a-f0-9]{8}\.css)")
    need: set[str] = set()
    for p in ROOT.glob("*.js"):
        need.update(pat.findall(p.read_text(encoding="utf-8", errors="replace")))
    return sorted(n for n in need if n not in on_disk)


def main() -> int:
    miss = missing_index_css()
    if NEEDLE not in {p.name for p in ROOT.iterdir()}:
        print("FAIL missing", NEEDLE)
        return 1
    if miss:
        print("FAIL missing css", miss)
        return 1
    print("ok", NEEDLE, "and", "all index.*.css from js")
    return 0


if __name__ == "__main__":
    sys.exit(main())
