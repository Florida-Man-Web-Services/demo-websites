"""Curated connect-not-book NAP for Hipp + Regal.

Demo `businesses` is the no-website outreach list — these venues are not
on it. Speak the phone; never invent showtimes. Not a licensed cinema feed.
"""

from __future__ import annotations

from businesses import slugify

# Phones from public listings (Visit Gainesville / Chamber / mall directory).
# Regal Royal Park + Butler share Regal's national showtimes line.
VENUES: list[dict[str, object]] = [
    {
        "name": "Hippodrome Theatre",
        "slug": "hippodrome-theatre",
        "aliases": (
            "hipp",
            "the hipp",
            "hippodrome",
            "the hippodrome",
            "hippodrome theatre",
            "hippodrome state theatre",
        ),
        "phone": "(352) 373-5968",
        "address": "25 SE 2nd Pl, Gainesville, FL 32601",
        "category": "arts",
    },
    {
        "name": "Regal Royal Park",
        "slug": "regal-royal-park",
        "aliases": (
            "regal royal park",
            "royal park",
            "royal park stadium",
            "regal royal park stadium 16",
        ),
        "phone": "(844) 462-7342",
        "address": "3702 W Newberry Rd, Gainesville, FL 32607",
        "category": "cinema",
    },
    {
        "name": "Regal Butler Town Center",
        "slug": "regal-butler-town-center",
        "aliases": (
            "regal butler",
            "butler town center",
            "regal butler town center",
        ),
        "phone": "(844) 462-7342",
        "address": "3101 SW 35th Blvd, Gainesville, FL 32608",
        "category": "cinema",
    },
    {
        "name": "Regal Celebration Pointe",
        "slug": "regal-celebration-pointe",
        "aliases": (
            "regal celebration pointe",
            "celebration pointe",
            "regal celebration",
        ),
        "phone": "(352) 373-2880",
        "address": "4901 SW 31st Pl, Gainesville, FL 32608",
        "category": "cinema",
    },
]

_REGAL_BARE = frozenset({"regal", "regal-cinemas", "regal-cinema"})


def _alias_slugs(venue: dict[str, object]) -> set[str]:
    slugs = {str(venue.get("slug") or "")}
    for alias in venue.get("aliases") or ():
        slugs.add(slugify(str(alias)))
    return {s for s in slugs if s}


def _suggestion(venue: dict[str, object]) -> dict[str, str]:
    return {"name": str(venue["name"]), "slug": str(venue["slug"])}


def _profile(venue: dict[str, object]) -> dict:
    return {
        "found": True,
        "name": str(venue["name"]),
        "slug": str(venue["slug"]),
        "category": str(venue.get("category") or ""),
        "address": str(venue.get("address") or ""),
        "phone": str(venue.get("phone") or ""),
        "connect_venue": True,
    }


def find_venue(query: str) -> dict | None:
    """Exact alias/slug match, or ambiguous Regal theaters. None = not ours."""
    q = slugify(query or "")
    if not q:
        return None
    if q in _REGAL_BARE:
        regal = [v for v in VENUES if "regal" in str(v["slug"])]
        return {
            "found": False,
            "suggestions": [_suggestion(v) for v in regal],
        }
    hits = [v for v in VENUES if q in _alias_slugs(v)]
    if len(hits) == 1:
        return _profile(hits[0])
    if len(hits) > 1:
        return {
            "found": False,
            "suggestions": [_suggestion(v) for v in hits],
        }
    return None
