#!/usr/bin/env python3
"""Regenerate stale AI 411 personal pages (cron / operator).

Usage (cluster or laptop with /data mounts):

  PERSONAL_PAGES_REGISTRY=/data/personal-pages.json \\
  PERSONAL_PAGES_DIR=/data/personal-pages \\
  CALLERS_PATH=/data/callers.json \\
  python scripts/regen_personal_pages.py

Exit 0 always; prints JSON summary on stdout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "mcp-server"), str(ROOT / "voice-agent")]

import personal_pages  # noqa: E402


def main() -> int:
    result = personal_pages.regen_all_stale()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
