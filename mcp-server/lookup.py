"""Resolve a caller's business by name, slug, or phone, with did-you-mean."""

import difflib

from businesses import all_businesses, by_phone_all, by_slug, slugify


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
    b = by_slug(slugify(q))
    if b:
        return _profile(b)
    slugs = {x.slug: x for x in all_businesses()}
    close = difflib.get_close_matches(slugify(q), list(slugs), n=3, cutoff=0.5)
    return {
        "found": False,
        "suggestions": [{"name": slugs[s].name, "slug": s} for s in close],
    }
