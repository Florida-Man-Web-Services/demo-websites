#!/usr/bin/env python3
"""Ingest Visit Gainesville tribe events into the AI 411 events store.

Cron-friendly:
  - exit 0 on success
  - single stable stdout digest line (no timestamps)
  - stderr for human errors

Examples:
  python3 scripts/ingest_visitgainesville_events.py
  python3 scripts/ingest_visitgainesville_events.py --out /tmp/events.json
  python3 scripts/ingest_visitgainesville_events.py --dry-run --max-pages 2
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

import events  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out",
        default="",
        help="Write store to this path (sets EVENTS_PATH). Default: events._store_path()",
    )
    ap.add_argument("--per-page", type=int, default=50, help="Tribe API per_page (max 50)")
    ap.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Cap pages fetched (0 = all pages)",
    )
    ap.add_argument(
        "--start-date",
        default="",
        help="Optional tribe start_date filter (YYYY-MM-DD)",
    )
    ap.add_argument(
        "--days-ahead",
        type=int,
        default=180,
        help="Keep occurrences starting within N days (0 = no horizon filter)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch+map only; do not write the store",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="Also print full result JSON on stdout after the digest line",
    )
    args = ap.parse_args(argv)

    if args.out:
        out = Path(args.out).expanduser().resolve()
        os.environ["EVENTS_PATH"] = str(out)
        events.EVENTS_PATH = out

    max_pages = args.max_pages if args.max_pages > 0 else None
    days_ahead = args.days_ahead if args.days_ahead > 0 else None
    start_date = (args.start_date or "").strip() or None

    result = events.ingest_visitgainesville(
        per_page=args.per_page,
        max_pages=max_pages,
        start_date=start_date,
        days_ahead=days_ahead,
        dry_run=args.dry_run,
    )

    if not result.get("ok"):
        print(result.get("error") or "ingest failed", file=sys.stderr)
        # Still try to emit prior digest if store readable
        try:
            print(f"DIGEST {events.stable_events_digest()}")
        except Exception:  # noqa: BLE001
            print("DIGEST error=1")
        return 1

    digest = result.get("digest") or events.stable_events_digest()
    # Stable single-line summary for monitors / CronJob logs
    print(
        "DIGEST "
        f"{digest} "
        f"fetched_raw={result.get('fetched_raw', 0)} "
        f"mapped={result.get('mapped', result.get('visitgainesville', 0))} "
        f"pages={result.get('pages', 0)} "
        f"dropped_nonlocal={result.get('dropped_nonlocal', 0)} "
        f"dropped_horizon={result.get('dropped_horizon', 0)} "
        f"purged_seed={result.get('purged_seed', 0)} "
        f"preserved={result.get('preserved', 0)} "
        f"dry_run={1 if result.get('dry_run') else 0}"
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
