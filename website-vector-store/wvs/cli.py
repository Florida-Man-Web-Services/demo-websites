"""Command line for the Gainesville website vector store.

    python -m wvs.cli build [--limit N] [--force]   # crawl + embed + upsert
    python -m wvs.cli update                          # incremental (only stale/new)
    python -m wvs.cli query "who does emergency AC repair" [-k 8]
    python -m wvs.cli stats
    python -m wvs.cli prospects                       # businesses with no website (leads)
"""
from __future__ import annotations

import argparse
import json
import sys

from .config import CONFIG
from .embed import get_embedder
from .pipeline import build
from .store import VectorStore


def main(argv=None):
    p = argparse.ArgumentParser(prog="wvs", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="crawl + embed + upsert all businesses")
    b.add_argument("--limit", type=int, default=None)
    b.add_argument("--force", action="store_true", help="re-crawl even if fresh")

    sub.add_parser("update", help="incremental refresh (skip fresh, re-crawl stale)")

    q = sub.add_parser("query", help="semantic search over indexed sites")
    q.add_argument("text")
    q.add_argument("-k", type=int, default=8)

    sub.add_parser("stats", help="index counts")
    pr = sub.add_parser("prospects", help="businesses with no website (sales leads)")
    pr.add_argument("--limit", type=int, default=50)

    args = p.parse_args(argv)

    if args.cmd == "build":
        build(CONFIG, limit=args.limit, force=args.force)
    elif args.cmd == "update":
        build(CONFIG, only_stale=True)
    elif args.cmd == "query":
        emb = get_embedder(CONFIG)
        store = VectorStore(CONFIG.store_path)
        hits = store.query(emb.embed([args.text])[0], k=args.k)
        for h in hits:
            print(f"{h['score']:.3f}  {h['name']}  <{h['url']}>")
            print(f"        {h['text'][:160]}...")
        store.close()
    elif args.cmd == "stats":
        store = VectorStore(CONFIG.store_path)
        print(json.dumps(store.stats(), indent=2))
        store.close()
    elif args.cmd == "prospects":
        store = VectorStore(CONFIG.store_path)
        for row in store.prospects(args.limit):
            print(f"{row['name']}")
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
