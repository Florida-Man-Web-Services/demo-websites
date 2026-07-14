"""Configuration for the Gainesville website vector store.

Everything is env-driven with sensible local-first defaults, so it runs offline
out of the box (hashing embedder + local sqlite store) and upgrades to a real
embedding provider by setting env vars.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent  # demo-websites/


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Config:
    # --- business sources (JSON lists of Gainesville businesses) ---
    # Each source is a path to a JSON array of business dicts. A business may
    # carry a "website" field; those without one are tracked as sales prospects.
    sources: list[Path] = field(default_factory=lambda: [
        REPO / "gainesville-no-website" / "gainesville_no_website.json",
        # add an "all Gainesville businesses" list (with `website` fields) here:
        REPO / "website-vector-store" / "data" / "gainesville_businesses.json",
    ])

    store_path: Path = field(default_factory=lambda: Path(
        _env("WVS_STORE", str(REPO / "website-vector-store" / "data" / "wvs.sqlite"))))

    # --- embeddings ---
    # WVS_EMBED_PROVIDER: "hashing" (offline default) | "openai" (OpenAI-compatible)
    embed_provider: str = field(default_factory=lambda: _env("WVS_EMBED_PROVIDER", "hashing"))
    embed_model: str = field(default_factory=lambda: _env("WVS_EMBED_MODEL", "text-embedding-3-small"))
    embed_base_url: str = field(default_factory=lambda: _env("WVS_EMBED_BASE_URL", "https://api.openai.com/v1"))
    embed_api_key: str = field(default_factory=lambda: _env("WVS_EMBED_API_KEY", ""))
    embed_dim: int = field(default_factory=lambda: int(_env("WVS_EMBED_DIM", "512")))  # hashing dim; openai overrides

    # --- crawl politeness / scope ---
    user_agent: str = field(default_factory=lambda: _env(
        "WVS_UA", "FloridaManWebServices-Indexer/1.0 (+https://floridamanweb.online)"))
    max_pages_per_site: int = field(default_factory=lambda: int(_env("WVS_MAX_PAGES", "5")))
    request_timeout: int = field(default_factory=lambda: int(_env("WVS_TIMEOUT", "15")))
    respect_robots: bool = field(default_factory=lambda: _env("WVS_ROBOTS", "1") == "1")

    # --- freshness: skip re-crawling a site indexed within this many days ---
    freshness_days: int = field(default_factory=lambda: int(_env("WVS_FRESHNESS_DAYS", "7")))

    # optional website discovery via Google Places Details (needs a key + place_id)
    places_api_key: str = field(default_factory=lambda: _env("WVS_PLACES_API_KEY", ""))


CONFIG = Config()
