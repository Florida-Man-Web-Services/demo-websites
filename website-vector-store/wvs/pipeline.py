"""The build pipeline: businesses → crawl → chunk → embed → upsert.

Incremental by design: a site indexed within `freshness_days` (and unchanged by
content hash) is skipped, so scheduled runs only do real work on new/stale sites.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .config import Config, CONFIG
from .crawl import fetch_site, chunk_text
from .embed import get_embedder
from .sources import load_businesses, resolve_website
from .store import VectorStore


def _fresh(state, freshness_days) -> bool:
    if not state or state.get("status") != "indexed" or not state.get("fetched_at"):
        return False
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(state["fetched_at"])
        return age.days < freshness_days
    except ValueError:
        return False


def build(cfg: Config = CONFIG, limit: int | None = None, force: bool = False,
          only_stale: bool = False, log=print) -> dict:
    businesses = load_businesses(cfg.sources)
    store = VectorStore(cfg.store_path)
    embedder = get_embedder(cfg)
    counts = {"indexed": 0, "skipped_fresh": 0, "prospects": 0, "errors": 0, "unchanged": 0}

    todo = businesses[:limit] if limit else businesses
    log(f"[wvs] {len(businesses)} businesses; processing {len(todo)} "
        f"(provider={cfg.embed_provider})")

    for biz in todo:
        website = resolve_website(biz, cfg.places_api_key)
        if not website:
            store.mark(biz.id, biz.name, None, "prospect")
            counts["prospects"] += 1
            continue

        state = store.business_state(biz.id)
        if not force and _fresh(state, cfg.freshness_days):
            counts["skipped_fresh"] += 1
            continue

        content = fetch_site(website, cfg)
        if content is None:
            store.mark(biz.id, biz.name, website, "error")
            counts["errors"] += 1
            log(f"[wvs] error   {biz.name} <{website}>")
            continue

        if state and state.get("content_hash") == content.content_hash:
            counts["unchanged"] += 1  # site unchanged; leave existing vectors
            continue

        chunks = chunk_text(content.text)
        vectors = embedder.embed(chunks)
        store.upsert_business(biz.id, biz.name, biz.category, website, chunks,
                              vectors, content.fetched_at, content.content_hash)
        counts["indexed"] += 1
        log(f"[wvs] indexed {biz.name} — {len(chunks)} chunks <{website}>")

    log(f"[wvs] done: {counts}")
    store.close()
    return counts
