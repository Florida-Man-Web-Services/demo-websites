"""Load and normalize the Gainesville business list from one or more JSON sources.

A business with a resolvable website is a *crawl target* (its site gets indexed);
a business without one is a *prospect* (a sales lead for FMWS). Both are tracked.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


@dataclass
class Business:
    id: str
    name: str
    address: str = ""
    category: str = ""
    phone: str = ""
    website: Optional[str] = None
    source: str = ""

    @property
    def has_website(self) -> bool:
        return bool(self.website)

    def dict(self) -> dict:
        return asdict(self)


def _slug(name: str, address: str) -> str:
    base = f"{name}-{address}".lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return base[:80] or "biz"


def _normalize(raw: dict, source: str) -> Optional[Business]:
    name = (raw.get("name") or raw.get("business_name") or "").strip()
    if not name:
        return None
    address = (raw.get("address") or "").strip()
    # accept several common field spellings for the website
    website = (raw.get("website") or raw.get("url") or raw.get("homepage") or "").strip() or None
    if website and not website.startswith(("http://", "https://")):
        website = "https://" + website
    return Business(
        id=_slug(name, address),
        name=name,
        address=address,
        category=(raw.get("category_label") or raw.get("category") or raw.get("search_category") or "").strip(),
        phone=(raw.get("phone") or "").strip(),
        website=website,
        source=source,
    )


def load_businesses(sources: list[Path]) -> list[Business]:
    """Merge all sources, de-duplicating by id (a website-bearing record wins)."""
    by_id: dict[str, Business] = {}
    for path in sources:
        if not Path(path).exists():
            continue
        try:
            data = json.loads(Path(path).read_text())
        except (json.JSONDecodeError, OSError):
            continue
        records = data if isinstance(data, list) else data.get("businesses", [])
        for raw in records:
            if not isinstance(raw, dict):
                continue
            biz = _normalize(raw, source=Path(path).name)
            if not biz:
                continue
            existing = by_id.get(biz.id)
            # prefer the record that carries a website
            if existing is None or (biz.has_website and not existing.has_website):
                by_id[biz.id] = biz
    return list(by_id.values())


def resolve_website(biz: Business, places_api_key: str = "") -> Optional[str]:
    """Return the business's website. Passthrough if already known; otherwise an
    optional Google Places Details lookup can be wired here (needs a place_id +
    key). Left as a hook so the offline default never makes network calls."""
    if biz.website:
        return biz.website
    # Discovery hook: with a Places API key you'd resolve name+address -> website.
    # Intentionally not implemented for the offline default.
    return None
