#!/usr/bin/env python3
"""Ingest allowlisted Visit Gainesville WP pages into the evergreen activities store.

  AI411_EVERGREEN_INGEST_ENABLED=1 python3 scripts/ingest_visitgainesville_activities.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_DIR = REPO_ROOT / "mcp-server"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

import activities  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="", help="ACTIVITIES_PATH override")
    args = ap.parse_args(argv)
    if args.out:
        os.environ["ACTIVITIES_PATH"] = str(Path(args.out).expanduser().resolve())
    os.environ.setdefault("AI411_EVERGREEN_INGEST_ENABLED", "1")
    result = activities.ingest_visitgainesville_pages()
    print(json.dumps({k: result[k] for k in result if k != "rows"}, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
