"""Load the Gainesville business list and resolve businesses by slug or phone.

Primary source is correspondences/outreach-data.csv (richest: demo URLs,
phones, ratings). Falls back to the tracked gainesville_no_website.json if the
CSV isn't present. call-order.csv supplies the prioritized outbound queue.
"""

import csv
import json
import re
from dataclasses import dataclass, field

import config


def slugify(name: str) -> str:
    """Match the slug scheme used for generated-sites/*.html filenames."""
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def normalize_phone(phone: str) -> str:
    """Reduce any phone formatting to a bare 10-digit US number.

    Drops a leading US country code and any trailing extension digits
    ('352-555-1234 x67' -> '3525551234') so extension/long entries stay
    dialable and still match an inbound 10-digit caller ID.
    """
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    elif len(digits) == 11:
        digits = digits[:10]  # 10 digits + a 1-digit extension, no country code
    elif len(digits) > 11:
        digits = digits[1:11] if digits.startswith("1") else digits[:10]
    return digits


@dataclass
class Business:
    name: str
    category: str = ""
    phone: str = ""
    address: str = ""
    rating: str = ""
    demo_url: str = ""
    slug: str = field(default="")
    google_maps_url: str = ""
    shared_demo: bool = False

    def __post_init__(self):
        if not self.slug:
            self.slug = slugify(self.name)
        if not self.demo_url:
            self.demo_url = f"{config.DEMO_BASE_URL}/{self.slug}.html"


def load_businesses() -> list[Business]:
    if config.OUTREACH_CSV.exists():
        with open(config.OUTREACH_CSV, newline="", encoding="utf-8") as f:
            return [
                Business(
                    name=row["name"],
                    category=row.get("category", ""),
                    phone=row.get("phone", ""),
                    address=row.get("address", ""),
                    rating=row.get("rating", ""),
                    demo_url=row.get("demo_url", ""),
                    google_maps_url=row.get("google_maps_url", ""),
                    shared_demo=row.get("shared_demo", "").strip().lower()
                    in ("yes", "true", "1"),
                )
                for row in csv.DictReader(f)
            ]
    if config.BUSINESS_JSON.exists():
        data = json.loads(config.BUSINESS_JSON.read_text(encoding="utf-8"))
        return [
            Business(
                name=b["name"],
                category=b.get("search_category", b.get("category_label", "")),
                phone=b.get("phone", ""),
                address=b.get("address", ""),
                rating=b.get("rating", ""),
                google_maps_url=b.get("google_maps_url", ""),
            )
            for b in data
        ]
    raise SystemExit(
        f"No business data found at {config.OUTREACH_CSV} or {config.BUSINESS_JSON}"
    )


_BUSINESSES: list[Business] | None = None


def all_businesses() -> list[Business]:
    global _BUSINESSES
    if _BUSINESSES is None:
        _BUSINESSES = load_businesses()
    return _BUSINESSES


def by_slug(slug: str) -> Business | None:
    return next((b for b in all_businesses() if b.slug == slug), None)


def by_phone(phone: str) -> Business | None:
    """Match an inbound caller ID to a business — recognizes callbacks."""
    digits = normalize_phone(phone)
    if not digits:
        return None
    return next(
        (b for b in all_businesses() if normalize_phone(b.phone) == digits), None
    )


def by_phone_all(phone: str) -> list[Business]:
    """All businesses sharing this phone number (same match as by_phone)."""
    digits = normalize_phone(phone)
    if not digits:
        return []
    return [b for b in all_businesses() if normalize_phone(b.phone) == digits]


def call_queue() -> list[dict]:
    """The prioritized outbound list (rank order) from call-order.csv."""
    if not config.CALL_ORDER_CSV.exists():
        return []
    with open(config.CALL_ORDER_CSV, newline="", encoding="utf-8") as f:
        return [row for row in csv.DictReader(f) if row.get("channel") == "call"]
