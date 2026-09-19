"""Typed kitchen-sink CMS document for a hosted-business tenant.

Wave-1 publication allows identity, hours, homepage blocks, services/menu,
FAQ, forms, and SEO. Staff, specials, events, media, and extra pages may
exist on a draft; publishing them as visible content is rejected.
Missing NAP/hours/prices stay missing. Never invent values.
"""

from __future__ import annotations

import copy
import re
import unicodedata
from typing import Any

SCHEMA_VERSION = 1
MAX_TEXT = 4_000
MAX_SHORT = 200
MAX_SLUG = 80
MAX_COLLECTION = 50
MAX_BLOCKS = 30
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
PROVENANCE = frozenset({"owner", "registry"})
WAVE1_VISIBLE_COLLECTIONS = frozenset({"services", "faq"})
LATER_COLLECTIONS = frozenset({"staff", "specials", "events", "media"})
HOME_BLOCK_TYPES = frozenset({"hero", "text", "hours", "services", "faq", "contact"})
ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HTML = re.compile(r"<\s*/?\s*[a-z]", re.I)

# Public projection never includes these keys.
PRIVATE_KEYS = frozenset({"provenance", "owner_notes", "private_contact", "draft_only"})


class CmsValidationError(ValueError):
    """Raised when a CMS document is invalid. Message must not echo secrets."""


def canonical_slug(value: str) -> str:
    if not isinstance(value, str):
        raise CmsValidationError("slug must be text")
    slug = value.strip().lower()
    if not slug or len(slug) > MAX_SLUG or ".." in slug or "/" in slug or "\\" in slug:
        raise CmsValidationError("slug is invalid")
    if not ID_RE.match(slug):
        raise CmsValidationError("slug is invalid")
    return slug


def _text(value: Any, *, field: str, maximum: int = MAX_TEXT, required: bool = False, allow_empty: bool = True) -> str | None:
    if value is None:
        if required:
            raise CmsValidationError(f"{field} is required")
        return None
    if not isinstance(value, str):
        raise CmsValidationError(f"{field} must be text")
    text = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n").strip()
    if _CONTROL.search(text) or _HTML.search(text):
        raise CmsValidationError(f"{field} must not contain markup")
    if len(text) > maximum:
        raise CmsValidationError(f"{field} is too long")
    if required and not text:
        raise CmsValidationError(f"{field} is required")
    if not text:
        return None if allow_empty else ""
    return text


def _id(value: Any, *, field: str) -> str:
    text = _text(value, field=field, maximum=64, required=True)
    assert text is not None
    if not ID_RE.match(text):
        raise CmsValidationError(f"{field} is invalid")
    return text


def _bool(value: Any, *, field: str, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise CmsValidationError(f"{field} must be a boolean")
    return value


def _int(value: Any, *, field: str, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise CmsValidationError(f"{field} must be an integer")
    return value


def empty_document(slug: str, name: str | None = None) -> dict[str, Any]:
    slug = canonical_slug(slug)
    name_text = _text(name, field="identity.name", maximum=MAX_SHORT) if name else None
    return {
        "schema_version": SCHEMA_VERSION,
        "slug": slug,
        "identity": {
            "name": name_text,
            "address": None,
            "public_phone": None,
            "website": None,
            "front_desk_contact": None,
            "links": [],
            "provenance": {},
        },
        "hours": {
            "timezone": None,
            "weekly": {day: [] for day in WEEKDAYS},
            "exceptions": [],
            "unknown": True,
        },
        "pages": [
            {
                "id": "home",
                "slug": "home",
                "title": name_text or "Home",
                "visible": True,
                "blocks": [],
            }
        ],
        "services": {"groups": []},
        "staff": [],
        "faq": [],
        "specials": [],
        "events": [],
        "media": [],
        "forms": {
            "contact": {"enabled": True},
            "message": {"enabled": True},
            "appointment": {"enabled": True},
        },
        "seo": {
            "title": name_text,
            "description": None,
            "canonical_path": "/",
            "indexable": True,
        },
    }


def _provenance_map(raw: Any) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise CmsValidationError("provenance must be a map")
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not ID_RE.match(key.replace("_", "-")) and not re.match(r"^[a-z_]+$", key):
            raise CmsValidationError("provenance key is invalid")
        if value not in PROVENANCE:
            raise CmsValidationError("provenance value is invalid")
        out[key] = value
    return out


def _safe_http_url(value: Any, *, field: str) -> str | None:
    text = _text(value, field=field, maximum=500)
    if text is None:
        return None
    lowered = text.lower()
    if not (lowered.startswith("https://") or lowered.startswith("http://")):
        raise CmsValidationError(f"{field} must be an http(s) URL")
    if any(ch.isspace() for ch in text) or "javascript:" in lowered:
        raise CmsValidationError(f"{field} is not a safe URL")
    return text


def _identity(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    links_in = data.get("links") or []
    if not isinstance(links_in, list) or len(links_in) > 10:
        raise CmsValidationError("identity.links is invalid")
    links = []
    for item in links_in:
        if not isinstance(item, dict):
            raise CmsValidationError("identity.links is invalid")
        label = _text(item.get("label"), field="link.label", maximum=MAX_SHORT, required=True)
        url = _safe_http_url(item.get("url"), field="link.url")
        if url is None:
            raise CmsValidationError("link.url is required")
        links.append({"label": label, "url": url})
    return {
        "name": _text(data.get("name"), field="identity.name", maximum=MAX_SHORT),
        "address": _text(data.get("address"), field="identity.address", maximum=500),
        "public_phone": _text(data.get("public_phone"), field="identity.public_phone", maximum=32),
        "website": _safe_http_url(data.get("website"), field="identity.website"),
        "front_desk_contact": _text(
            data.get("front_desk_contact"), field="identity.front_desk_contact", maximum=32
        ),
        "links": links,
        "provenance": _provenance_map(data.get("provenance")),
    }


def _interval(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise CmsValidationError("hours interval is invalid")
    open_at = _text(raw.get("open"), field="hours.open", maximum=5, required=True)
    close_at = _text(raw.get("close"), field="hours.close", maximum=5, required=True)
    assert open_at and close_at
    if not TIME_RE.match(open_at) or not TIME_RE.match(close_at):
        raise CmsValidationError("hours interval is invalid")
    return {"open": open_at, "close": close_at}


def _hours(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    weekly_in = data.get("weekly") or {}
    if not isinstance(weekly_in, dict):
        raise CmsValidationError("hours.weekly is invalid")
    weekly = {}
    for day in WEEKDAYS:
        items = weekly_in.get(day) or []
        if not isinstance(items, list) or len(items) > 6:
            raise CmsValidationError("hours.weekly is invalid")
        weekly[day] = [_interval(item) for item in items]
    exceptions_in = data.get("exceptions") or []
    if not isinstance(exceptions_in, list) or len(exceptions_in) > MAX_COLLECTION:
        raise CmsValidationError("hours.exceptions is invalid")
    exceptions = []
    for item in exceptions_in:
        if not isinstance(item, dict):
            raise CmsValidationError("hours.exceptions is invalid")
        date = _text(item.get("date"), field="hours.exception.date", maximum=10, required=True)
        assert date is not None
        if not DATE_RE.match(date):
            raise CmsValidationError("hours.exception.date is invalid")
        closed = _bool(item.get("closed"), field="hours.exception.closed")
        intervals = [] if closed else [_interval(x) for x in (item.get("intervals") or [])]
        exceptions.append(
            {
                "date": date,
                "closed": closed,
                "intervals": intervals,
                "note": _text(item.get("note"), field="hours.exception.note", maximum=MAX_SHORT),
            }
        )
    timezone = _text(data.get("timezone"), field="hours.timezone", maximum=64)
    unknown = _bool(data.get("unknown"), field="hours.unknown", default=timezone is None)
    return {"timezone": timezone, "weekly": weekly, "exceptions": exceptions, "unknown": unknown}


def _blocks(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_BLOCKS:
        raise CmsValidationError("page.blocks is invalid")
    blocks = []
    for item in raw:
        if not isinstance(item, dict):
            raise CmsValidationError("page.blocks is invalid")
        btype = _text(item.get("type"), field="block.type", maximum=32, required=True)
        if btype not in HOME_BLOCK_TYPES:
            raise CmsValidationError("unsupported block type")
        blocks.append(
            {
                "type": btype,
                "heading": _text(item.get("heading"), field="block.heading", maximum=MAX_SHORT),
                "body": _text(item.get("body"), field="block.body"),
            }
        )
    return blocks


def _pages(raw: Any) -> list[dict[str, Any]]:
    pages_in = raw if isinstance(raw, list) and raw else [{"id": "home", "slug": "home", "title": "Home", "visible": True, "blocks": []}]
    if not isinstance(pages_in, list) or len(pages_in) > 20:
        raise CmsValidationError("pages is invalid")
    pages = []
    seen = set()
    home = False
    for item in pages_in:
        if not isinstance(item, dict):
            raise CmsValidationError("pages is invalid")
        pid = _id(item.get("id"), field="page.id")
        if pid in seen:
            raise CmsValidationError("duplicate page id")
        seen.add(pid)
        page = {
            "id": pid,
            "slug": canonical_slug(item.get("slug") or pid),
            "title": _text(item.get("title"), field="page.title", maximum=MAX_SHORT) or "Untitled",
            "visible": _bool(item.get("visible"), field="page.visible", default=True),
            "blocks": _blocks(item.get("blocks")),
        }
        if pid == "home":
            home = True
        pages.append(page)
    if not home:
        raise CmsValidationError("home page is required")
    return pages


def _service_item(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CmsValidationError("service item is invalid")
    price = raw.get("price")
    if price is not None and not isinstance(price, (int, float)):
        raise CmsValidationError("service price is invalid")
    if isinstance(price, bool):
        raise CmsValidationError("service price is invalid")
    currency = _text(raw.get("currency"), field="service.currency", maximum=8)
    return {
        "id": _id(raw.get("id"), field="service.id"),
        "name": _text(raw.get("name"), field="service.name", maximum=MAX_SHORT, required=True),
        "description": _text(raw.get("description"), field="service.description"),
        "price": None if price is None else float(price),
        "currency": currency,
        "available": _bool(raw.get("available"), field="service.available", default=True),
    }


def _services(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    groups_in = data.get("groups") or []
    if not isinstance(groups_in, list) or len(groups_in) > MAX_COLLECTION:
        raise CmsValidationError("services.groups is invalid")
    groups = []
    seen = set()
    for group in groups_in:
        if not isinstance(group, dict):
            raise CmsValidationError("services.groups is invalid")
        gid = _id(group.get("id"), field="service.group.id")
        if gid in seen:
            raise CmsValidationError("duplicate service group id")
        seen.add(gid)
        items_in = group.get("items") or []
        if not isinstance(items_in, list) or len(items_in) > MAX_COLLECTION:
            raise CmsValidationError("service items is invalid")
        item_ids = set()
        items = []
        for item in items_in:
            parsed = _service_item(item)
            if parsed["id"] in item_ids:
                raise CmsValidationError("duplicate service id")
            item_ids.add(parsed["id"])
            items.append(parsed)
        groups.append(
            {
                "id": gid,
                "name": _text(group.get("name"), field="service.group.name", maximum=MAX_SHORT, required=True),
                "items": items,
            }
        )
    return {"groups": groups}


def _faq(raw: Any) -> list[dict[str, Any]]:
    items = raw or []
    if not isinstance(items, list) or len(items) > MAX_COLLECTION:
        raise CmsValidationError("faq is invalid")
    out = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise CmsValidationError("faq is invalid")
        fid = _id(item.get("id"), field="faq.id")
        if fid in seen:
            raise CmsValidationError("duplicate faq id")
        seen.add(fid)
        out.append(
            {
                "id": fid,
                "question": _text(item.get("question"), field="faq.question", maximum=MAX_SHORT, required=True),
                "answer": _text(item.get("answer"), field="faq.answer", required=True),
                "visible": _bool(item.get("visible"), field="faq.visible", default=True),
                "order": _int(item.get("order"), field="faq.order"),
            }
        )
    return out


def _named_collection(raw: Any, *, kind: str, extra: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    items = raw or []
    if not isinstance(items, list) or len(items) > MAX_COLLECTION:
        raise CmsValidationError(f"{kind} is invalid")
    out = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise CmsValidationError(f"{kind} is invalid")
        iid = _id(item.get("id"), field=f"{kind}.id")
        if iid in seen:
            raise CmsValidationError(f"duplicate {kind} id")
        seen.add(iid)
        row = {
            "id": iid,
            "title": _text(item.get("title") or item.get("name"), field=f"{kind}.title", maximum=MAX_SHORT),
            "name": _text(item.get("name"), field=f"{kind}.name", maximum=MAX_SHORT),
            "role": _text(item.get("role"), field=f"{kind}.role", maximum=MAX_SHORT),
            "body": _text(item.get("body") or item.get("biography") or item.get("description"), field=f"{kind}.body"),
            "visible": _bool(item.get("visible"), field=f"{kind}.visible", default=False),
            "start": _text(item.get("start"), field=f"{kind}.start", maximum=40),
            "end": _text(item.get("end"), field=f"{kind}.end", maximum=40),
            "url": _safe_http_url(item.get("url"), field=f"{kind}.url") if item.get("url") else None,
            "alt": _text(item.get("alt"), field=f"{kind}.alt", maximum=MAX_SHORT),
        }
        for key in extra:
            if key in item:
                row[key] = item[key]
        out.append(row)
    return out


def _forms(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    out = {}
    for name in ("contact", "message", "appointment"):
        spec = data.get(name) or {}
        if not isinstance(spec, dict):
            raise CmsValidationError("forms is invalid")
        out[name] = {"enabled": _bool(spec.get("enabled"), field=f"forms.{name}.enabled", default=True)}
    return out


def _seo(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    path = _text(data.get("canonical_path"), field="seo.canonical_path", maximum=200) or "/"
    if not path.startswith("/") or ".." in path:
        raise CmsValidationError("seo.canonical_path is invalid")
    return {
        "title": _text(data.get("title"), field="seo.title", maximum=MAX_SHORT),
        "description": _text(data.get("description"), field="seo.description", maximum=320),
        "canonical_path": path,
        "indexable": _bool(data.get("indexable"), field="seo.indexable", default=True),
    }


def validate_document(doc: Any, *, for_publish: bool = False) -> dict[str, Any]:
    if not isinstance(doc, dict):
        raise CmsValidationError("document must be an object")
    version = doc.get("schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise CmsValidationError("unsupported schema_version")
    slug = canonical_slug(str(doc.get("slug") or ""))
    pages = _pages(doc.get("pages"))
    staff = _named_collection(doc.get("staff"), kind="staff")
    specials = _named_collection(doc.get("specials"), kind="specials")
    events = _named_collection(doc.get("events"), kind="events")
    media = _named_collection(doc.get("media"), kind="media")
    canonical = {
        "schema_version": SCHEMA_VERSION,
        "slug": slug,
        "identity": _identity(doc.get("identity")),
        "hours": _hours(doc.get("hours")),
        "pages": pages,
        "services": _services(doc.get("services")),
        "staff": staff,
        "faq": _faq(doc.get("faq")),
        "specials": specials,
        "events": events,
        "media": media,
        "forms": _forms(doc.get("forms")),
        "seo": _seo(doc.get("seo")),
    }
    if for_publish:
        _reject_unsupported_public(canonical)
    return canonical


def _reject_unsupported_public(doc: dict[str, Any]) -> None:
    extra_pages = [p for p in doc["pages"] if p["id"] != "home" and p.get("visible")]
    if extra_pages:
        raise CmsValidationError("additional public pages are not enabled")
    for kind in LATER_COLLECTIONS:
        if any(item.get("visible") for item in doc.get(kind) or []):
            raise CmsValidationError(f"{kind} cannot be published yet")
    for block in next(p for p in doc["pages"] if p["id"] == "home")["blocks"]:
        if block["type"] not in HOME_BLOCK_TYPES:
            raise CmsValidationError("unsupported visible block")


def public_projection(doc: dict[str, Any]) -> dict[str, Any]:
    """Owner-safe public JSON: no provenance, no later-wave hidden collections."""
    canonical = validate_document(doc, for_publish=False)
    identity = {k: v for k, v in canonical["identity"].items() if k != "provenance"}
    home = next(p for p in canonical["pages"] if p["id"] == "home")
    faq = [item for item in canonical["faq"] if item.get("visible")]
    faq.sort(key=lambda item: (item.get("order") or 0, item["id"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "slug": canonical["slug"],
        "identity": identity,
        "hours": canonical["hours"],
        "page": {"title": home["title"], "blocks": home["blocks"]},
        "services": canonical["services"],
        "faq": faq,
        "forms": canonical["forms"],
        "seo": canonical["seo"],
    }


def strip_private(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: strip_private(v) for k, v in value.items() if k not in PRIVATE_KEYS}
    if isinstance(value, list):
        return [strip_private(v) for v in value]
    return copy.deepcopy(value)
