"""Resolve a caller's business by name, slug, or phone, with did-you-mean."""

import difflib

from businesses import all_businesses, by_phone_all, by_slug, slugify

# ASR / nickname → catalog slug. Keep small; do not invent businesses.
_QUERY_ALIASES = {
    "entacto": "impacto",
    "entacto-club": "impacto",
    "impacto-club": "impacto",
    "uf-impacto": "impacto",
    "in-pacto": "impacto",
    "impact-o": "impacto",
}


def _profile(b) -> dict:
    return {
        "found": True,
        "name": b.name,
        "slug": b.slug,
        "category": b.category,
        "address": b.address,
        "phone": b.phone,
        "rating": b.rating,
        "demo_url": b.demo_url,
        "google_maps_url": b.google_maps_url,
        "shared_demo": b.shared_demo,
    }


def _profile_from_customer(cust: dict) -> dict | None:
    """Owner/page-manager row → speakable lookup. Never copy CID into NAP phone."""
    slug = (cust.get("slug") or "").strip()
    if not slug:
        return None
    b = by_slug(slug)
    if b:
        out = _profile(b)
    else:
        out = {
            "found": True,
            "name": (cust.get("business_name") or slug).strip(),
            "slug": slug,
            "category": (cust.get("category") or ""),
            "address": "",
            "phone": "",
            "rating": "",
            "demo_url": (cust.get("demo_url") or ""),
            "google_maps_url": "",
            "shared_demo": False,
        }
    # Catalog NAP stays if the site published one; manager CID is not NAP.
    out["owner_match"] = True
    if cust.get("contact_name"):
        out["contact_name"] = cust["contact_name"]
    if cust.get("demo_url") and not out.get("demo_url"):
        out["demo_url"] = cust["demo_url"]
    return out


def _customers_for_phone(query: str) -> list[dict]:
    try:
        import customers as customers_mod
    except Exception:
        return []
    try:
        return customers_mod.find_customers_for_phone(query)
    except Exception:
        return []


def find_business(query: str) -> dict:
    q = (query or "").strip()
    digits = sum(ch.isdigit() for ch in q)
    if digits >= 7:  # looks like a phone number
        matches = by_phone_all(q)
        if len(matches) == 1:
            return _profile(matches[0])
        if len(matches) > 1:
            return {
                "found": False,
                "ambiguous_phone": True,
                "suggestions": [{"name": b.name, "slug": b.slug} for b in matches],
            }
        owned = [c for c in _customers_for_phone(q) if (c.get("slug") or "").strip()]
        if len(owned) == 1:
            profile = _profile_from_customer(owned[0])
            if profile:
                return profile
        if len(owned) > 1:
            return {
                "found": False,
                "ambiguous_phone": True,
                "suggestions": [
                    {
                        "name": (c.get("business_name") or c.get("slug") or ""),
                        "slug": (c.get("slug") or ""),
                    }
                    for c in owned
                ],
            }
    b = by_slug(slugify(q))
    if not b:
        aliased = _QUERY_ALIASES.get(slugify(q))
        if aliased:
            b = by_slug(aliased)
    if b:
        return _profile(b)
    slugs = {x.slug: x for x in all_businesses()}
    close = difflib.get_close_matches(slugify(q), list(slugs), n=3, cutoff=0.5)
    return {
        "found": False,
        "suggestions": [{"name": slugs[s].name, "slug": s} for s in close],
    }
