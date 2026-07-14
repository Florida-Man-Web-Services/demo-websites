"""Offline end-to-end smoke test: no network, no API key.

Run:  python tests/test_smoke.py   (from the website-vector-store/ dir)
Exercises sources → clean/chunk → hashing-embed → sqlite store → semantic query.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wvs.sources import load_businesses  # noqa: E402
from wvs.crawl import _clean_text, chunk_text  # noqa: E402
from wvs.embed import HashingEmbedder  # noqa: E402
from wvs.store import VectorStore  # noqa: E402

SITES = {
    "coolbreeze-ac": ("CoolBreeze AC", "hvac",
        "<html><title>CoolBreeze AC</title><body><h1>Emergency air conditioning "
        "repair in Gainesville</h1><p>24/7 AC repair, heating, and duct cleaning.</p>"
        "<script>ignore()</script></body></html>"),
    "gator-tacos": ("Gator Tacos", "restaurant",
        "<html><title>Gator Tacos</title><body><p>Authentic street tacos, burritos, "
        "and margaritas in downtown Gainesville. Catering available.</p></body></html>"),
}


def run():
    emb = HashingEmbedder(dim=256)
    with tempfile.TemporaryDirectory() as d:
        store = VectorStore(Path(d) / "t.sqlite")

        # index synthetic sites (bypasses network; mirrors what pipeline.build does)
        for bid, (name, cat, html) in SITES.items():
            title, text = _clean_text(html)
            assert "ignore()" not in text, "script content must be stripped"
            chunks = chunk_text(text, size=200)
            store.upsert_business(bid, name, cat, f"https://{bid}.com", chunks,
                                  emb.embed(chunks), "2026-07-13T00:00:00Z", "hash1")

        # relevant retrieval
        hits = store.query(emb.embed(["who fixes broken air conditioning?"])[0], k=2)
        assert hits, "query returned nothing"
        assert hits[0]["name"] == "CoolBreeze AC", f"wrong top hit: {hits[0]['name']}"

        # idempotent re-index: re-indexing the same business must REPLACE, not append
        before = store.stats()["chunks"]
        title, text = _clean_text(SITES["coolbreeze-ac"][2])
        chunks = chunk_text(text, size=200)
        store.upsert_business("coolbreeze-ac", "CoolBreeze AC", "hvac",
                              "https://coolbreeze-ac.com", chunks, emb.embed(chunks),
                              "2026-07-13T01:00:00Z", "hash2")
        assert store.stats()["chunks"] == before, "re-index must replace, not duplicate"

        # prospect tracking
        store.mark("no-site-biz", "No Site Biz", None, "prospect")
        assert store.stats()["prospects"] == 1
        store.close()

    # sources loader against the real Gainesville data (if present)
    repo = Path(__file__).resolve().parent.parent.parent
    src = repo / "gainesville-no-website" / "gainesville_no_website.json"
    if src.exists():
        biz = load_businesses([src])
        assert len(biz) > 100, f"expected many Gainesville businesses, got {len(biz)}"
        assert all(not b.has_website for b in biz), "no-website list must have no sites"
        print(f"[smoke] loaded {len(biz)} Gainesville businesses from real data")

    print("[smoke] PASS — index, retrieve, re-index idempotency, prospects, sources")


if __name__ == "__main__":
    run()
