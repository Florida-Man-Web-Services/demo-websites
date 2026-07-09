"""Resolve a caller's business by name, slug, or phone, with did-you-mean."""

import difflib

from businesses import all_businesses, by_phone, by_slug, slugify


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
    }


def find_business(query: str) -> dict:
    q = (query or "").strip()
    digits = sum(ch.isdigit() for ch in q)
    if digits >= 7:  # looks like a phone number
        b = by_phone(q)
        if b:
            return _profile(b)
    b = by_slug(slugify(q))
    if b:
        return _profile(b)
    slugs = {x.slug: x for x in all_businesses()}
    close = difflib.get_close_matches(slugify(q), list(slugs), n=3, cutoff=0.5)
    return {
        "found": False,
        "suggestions": [{"name": slugs[s].name, "slug": s} for s in close],
    }
