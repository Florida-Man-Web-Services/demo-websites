"""AI 411 free personal pages — opt-in, memory-based, 24h regen.

Public mini-sites built only from caller profile fields the person chose to
remember with AI 411. Default OFF. Requires consent.memory_ok AND
consent.personal_page_ok.

Privacy hard rules:
  - Never put phone, email, full address, or raw private notes on the page.
  - Slug is an unguessable public token (not derived from phone alone).
  - Opt-out / forget_caller removes registry + HTML.

Storage
  PERSONAL_PAGES_REGISTRY  — JSON map phone → meta (default /data/personal-pages.json)
  PERSONAL_PAGES_DIR       — HTML files (default /data/personal-pages/)
  PERSONAL_PAGE_BASE_URL   — public URL prefix (e.g. https://voice…/me)
  PERSONAL_PAGE_TTL_HOURS  — regen interval (default 24)

Tools / API surface
  opt_in_personal_page(phone, …)
  opt_out_personal_page(phone)
  get_personal_page_status(phone)
  ensure_fresh / regenerate / render_public(slug)
"""

from __future__ import annotations

import copy
import html
import json
import os
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import callers

_DEFAULT_REGISTRY = Path("/data/personal-pages.json")
_FALLBACK_REGISTRY = (
    Path(__file__).resolve().parent.parent / "data" / "personal-pages.json"
)
_DEFAULT_DIR = Path("/data/personal-pages")
_FALLBACK_DIR = Path(__file__).resolve().parent.parent / "data" / "personal-pages"

PERSONAL_PAGES_REGISTRY = Path(
    os.getenv(
        "PERSONAL_PAGES_REGISTRY",
        str(
            _DEFAULT_REGISTRY
            if _DEFAULT_REGISTRY.parent.exists()
            else _FALLBACK_REGISTRY
        ),
    )
)
PERSONAL_PAGES_DIR = Path(
    os.getenv(
        "PERSONAL_PAGES_DIR",
        str(_DEFAULT_DIR if _DEFAULT_DIR.parent.exists() else _FALLBACK_DIR),
    )
)

PERSONAL_PAGE_BASE_URL = (
    os.getenv("PERSONAL_PAGE_BASE_URL", "").rstrip("/")
    or os.getenv("PUBLIC_BASE_URL", "https://voice.flmanbiosci.net").rstrip("/")
    + "/me"
)
PERSONAL_PAGE_TTL_HOURS = int(os.getenv("PERSONAL_PAGE_TTL_HOURS", "24"))

_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _now_iso() -> str:
    return _now().isoformat()


def _registry_path() -> Path:
    env = os.getenv("PERSONAL_PAGES_REGISTRY")
    if env:
        return Path(env)
    return Path(PERSONAL_PAGES_REGISTRY)


def _pages_dir() -> Path:
    env = os.getenv("PERSONAL_PAGES_DIR")
    if env:
        return Path(env)
    return Path(PERSONAL_PAGES_DIR)


def _base_url() -> str:
    env = os.getenv("PERSONAL_PAGE_BASE_URL")
    if env:
        return env.rstrip("/")
    pub = (os.getenv("PUBLIC_BASE_URL") or "https://voice.flmanbiosci.net").rstrip(
        "/"
    )
    return pub + "/me"


def _ttl_hours() -> int:
    try:
        return max(1, int(os.getenv("PERSONAL_PAGE_TTL_HOURS", str(PERSONAL_PAGE_TTL_HOURS))))
    except ValueError:
        return 24


def _normalize_phone(phone: str) -> str | None:
    return callers._normalize_phone(phone)  # noqa: SLF001 — shared E.164 rules


def _load_registry() -> dict[str, dict]:
    path = _registry_path()
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    if "pages" in data and isinstance(data["pages"], dict):
        return data["pages"]
    return data


def _save_registry(pages: dict[str, dict]) -> None:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = {"pages": pages, "updated_at": _now_iso()}
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(path)


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _public_url(slug: str) -> str:
    return f"{_base_url()}/{slug}/"


def _new_slug() -> str:
    # Unguessable public id; not reversible to phone.
    return "p-" + secrets.token_hex(8)


def _safe_name(profile: dict) -> str:
    for key in ("preferred_name", "display_name"):
        val = str(profile.get(key) or "").strip()
        if val:
            # First token only for display_name privacy (no full legal dump).
            if key == "display_name" and " " in val:
                return val.split()[0]
            return val
    return "Neighbor"


def _clean_list(items: Any, *, limit: int = 12) -> list[str]:
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for x in items:
        s = str(x or "").strip()
        if not s:
            continue
        # Drop anything that looks like a phone/email.
        if "@" in s or re.search(r"\d{7,}", s):
            continue
        if s not in out:
            out.append(s)
        if len(out) >= limit:
            break
    return out


def _public_payload(profile: dict, meta: dict) -> dict[str, Any]:
    """Fields safe to put on a public page."""
    prefs = profile.get("preferences") or {}
    interests = _clean_list(prefs.get("interests"))
    areas = _clean_list(prefs.get("preferred_areas"))
    avoid = _clean_list(prefs.get("avoid"), limit=6)
    topics = _clean_list(profile.get("last_topics"), limit=8)
    mobility = str(prefs.get("mobility") or "").strip()
    accessibility = str(prefs.get("accessibility") or "").strip()
    # Strip digits-heavy free text.
    if re.search(r"\d{7,}", mobility):
        mobility = ""
    if re.search(r"\d{7,}", accessibility):
        accessibility = ""
    headline = str(meta.get("headline") or "").strip()
    if not headline:
        if interests:
            headline = f"Into {', '.join(interests[:3])}"
        else:
            headline = "Gainesville local · AI 411 neighbor page"
    return {
        "name": _safe_name(profile),
        "headline": headline[:160],
        "interests": interests,
        "preferred_areas": areas,
        "avoid": avoid,
        "topics": topics,
        "mobility": mobility[:120],
        "accessibility": accessibility[:160],
        "generated_at": meta.get("last_generated_at") or _now_iso(),
        "ttl_hours": _ttl_hours(),
        "slug": meta.get("slug") or "",
    }


def _render_html(payload: dict) -> str:
    name = html.escape(payload.get("name") or "Neighbor")
    headline = html.escape(payload.get("headline") or "")
    gen = html.escape(str(payload.get("generated_at") or ""))
    ttl = int(payload.get("ttl_hours") or 24)
    interests = payload.get("interests") or []
    areas = payload.get("preferred_areas") or []
    avoid = payload.get("avoid") or []
    topics = payload.get("topics") or []
    mobility = html.escape(str(payload.get("mobility") or ""))
    accessibility = html.escape(str(payload.get("accessibility") or ""))

    def chips(items: list[str], empty: str) -> str:
        if not items:
            return f'<p class="empty">{html.escape(empty)}</p>'
        bits = "".join(
            f'<span class="chip">{html.escape(i)}</span>' for i in items
        )
        return f'<div class="chips">{bits}</div>'

    sections = []
    sections.append(
        f'<section><h2>Interests</h2>{chips(interests, "Still learning — call AI 411 to share what you like.")}</section>'
    )
    if areas:
        sections.append(
            f"<section><h2>Favorite areas</h2>{chips(areas, '')}</section>"
        )
    if avoid:
        sections.append(
            f"<section><h2>Rather skip</h2>{chips(avoid, '')}</section>"
        )
    if topics:
        sections.append(
            f"<section><h2>Lately talking about</h2>{chips(topics, '')}</section>"
        )
    extra = []
    if mobility:
        extra.append(f"<li><strong>Getting around:</strong> {mobility}</li>")
    if accessibility:
        extra.append(f"<li><strong>Access notes:</strong> {accessibility}</li>")
    if extra:
        sections.append(
            "<section><h2>How they roll</h2><ul>" + "".join(extra) + "</ul></section>"
        )

    body = "\n".join(sections)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="robots" content="noindex,nofollow" />
  <title>{name} · Gainesville AI 411</title>
  <meta name="description" content="Free personal page for {name} via Gainesville AI 411. Opt-in only; refreshed about every {ttl} hours." />
  <style>
    :root {{
      --bg: #0b1220; --card: #121a2b; --ink: #e8eefc; --muted: #9aabc8;
      --accent: #3dd6c6; --accent2: #f5b942; --line: rgba(255,255,255,.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; min-height: 100vh; color: var(--ink);
      font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
      background:
        radial-gradient(1000px 500px at 0% 0%, #1a3a4a 0%, transparent 55%),
        radial-gradient(800px 400px at 100% 10%, #3a2a10 0%, transparent 50%),
        var(--bg);
    }}
    .wrap {{ max-width: 720px; margin: 0 auto; padding: 2.5rem 1.25rem 4rem; }}
    .badge {{
      display: inline-block; font-size: .75rem; letter-spacing: .08em;
      text-transform: uppercase; color: var(--accent);
      border: 1px solid rgba(61,214,198,.35); padding: .35rem .65rem;
      border-radius: 999px; margin-bottom: 1rem;
    }}
    h1 {{ font-size: clamp(1.9rem, 4vw, 2.6rem); margin: 0 0 .5rem; line-height: 1.15; }}
    .lead {{ color: var(--muted); font-size: 1.1rem; line-height: 1.5; margin: 0 0 1.5rem; }}
    .card {{
      background: linear-gradient(180deg, rgba(255,255,255,.03), transparent 40%), var(--card);
      border: 1px solid var(--line); border-radius: 18px; padding: 1.35rem 1.25rem 1.5rem;
      box-shadow: 0 20px 60px rgba(0,0,0,.35); margin-bottom: 1rem;
    }}
    h2 {{ font-size: 1rem; letter-spacing: .04em; text-transform: uppercase;
         color: var(--accent2); margin: 0 0 .75rem; }}
    section + section {{ margin-top: 1.25rem; padding-top: 1.1rem; border-top: 1px solid var(--line); }}
    .chips {{ display: flex; flex-wrap: wrap; gap: .5rem; }}
    .chip {{
      background: rgba(61,214,198,.12); border: 1px solid rgba(61,214,198,.28);
      color: #b7fff6; padding: .4rem .7rem; border-radius: 999px; font-size: .92rem;
    }}
    .empty {{ color: var(--muted); margin: 0; }}
    ul {{ margin: 0; padding-left: 1.1rem; color: var(--muted); line-height: 1.55; }}
    footer {{ margin-top: 1.75rem; color: var(--muted); font-size: .8rem; line-height: 1.55; }}
    a {{ color: var(--accent); }}
    .meta {{ font-size: .85rem; color: var(--muted); margin-top: .75rem; }}
  </style>
</head>
<body>
  <main class="wrap">
    <div class="badge">Gainesville · AI 411 · Personal page</div>
    <h1>{name}</h1>
    <p class="lead">{headline}</p>
    <article class="card">
      {body}
      <p class="meta">Page rebuilt from what AI 411 remembers · last generated {gen} · refreshes about every {ttl} hours.</p>
    </article>
    <footer>
      Opt-in free personal page from <a href="https://ai411.floridamanweb.online/ai411/">Gainesville AI 411</a>.
      No phone numbers on this page. To change or remove it, call AI 411 and say
      “update my page” or “take my page down.” Not a business website product —
      for a free business demo, ask on the same line or the landing form.
    </footer>
  </main>
</body>
</html>
"""


def _write_html(slug: str, content: str) -> Path:
    d = _pages_dir()
    d.mkdir(parents=True, exist_ok=True)
    # Path safety: slug must be our token shape only.
    if not re.fullmatch(r"p-[a-f0-9]{16}", slug or ""):
        raise ValueError("invalid slug")
    path = d / f"{slug}.html"
    tmp = path.with_suffix(".html.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
    return path


def _delete_html(slug: str) -> None:
    if not slug or not re.fullmatch(r"p-[a-f0-9]{16}", slug):
        return
    path = _pages_dir() / f"{slug}.html"
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _raw_profile(phone_e164: str) -> dict | None:
    """Internal profile including memory fields (consent already checked by caller)."""
    try:
        with callers._lock:  # noqa: SLF001
            profiles = callers._load_store()  # noqa: SLF001
            prof = profiles.get(phone_e164)
            return copy.deepcopy(prof) if prof else None
    except Exception:
        return None


def _consent_flags(phone_e164: str) -> dict[str, bool]:
    prof = _raw_profile(phone_e164) or {}
    consent = prof.get("consent") or {}
    prefs = prof.get("preferences") or {}
    return {
        "memory_ok": bool(consent.get("memory_ok")),
        "personal_page_ok": bool(consent.get("personal_page_ok"))
        or bool(prefs.get("personal_page")),
    }


def _status_from_meta(phone: str, meta: dict | None, *, flags: dict | None = None) -> dict:
    flags = flags or _consent_flags(phone)
    if not meta or not meta.get("enabled"):
        return {
            "ok": True,
            "enabled": False,
            "memory_ok": flags.get("memory_ok", False),
            "personal_page_ok": flags.get("personal_page_ok", False),
            "url": "",
            "slug": "",
            "last_generated_at": None,
            "next_regen_at": None,
            "stale": False,
            "message": (
                "Personal page is off. Opt in after enabling memory if you want a "
                "free page built from what AI 411 knows about you — refreshed about "
                f"every {_ttl_hours()} hours."
            ),
        }
    last = _parse_iso(meta.get("last_generated_at"))
    ttl = timedelta(hours=_ttl_hours())
    next_at = (last + ttl) if last else _now()
    stale = last is None or _now() >= next_at
    slug = meta.get("slug") or ""
    return {
        "ok": True,
        "enabled": True,
        "memory_ok": flags.get("memory_ok", False),
        "personal_page_ok": flags.get("personal_page_ok", False),
        "url": _public_url(slug) if slug else "",
        "slug": slug,
        "last_generated_at": meta.get("last_generated_at"),
        "next_regen_at": next_at.isoformat() if next_at else None,
        "stale": stale,
        "headline": meta.get("headline") or "",
        "message": (
            f"Personal page is live at {_public_url(slug)}. "
            f"It rebuilds about every {_ttl_hours()} hours from remembered interests."
            if slug
            else "Enabled but not generated yet."
        ),
    }


def get_personal_page_status(phone: str) -> dict:
    """Speakable status for this phone. Never raises."""
    key = _normalize_phone(phone)
    if not key:
        return {"ok": False, "enabled": False, "error": "invalid or missing phone"}
    try:
        flags = _consent_flags(key)
        with _lock:
            meta = _load_registry().get(key)
        return _status_from_meta(key, meta, flags=flags)
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "enabled": False,
            "error": f"data unavailable ({e.__class__.__name__})",
        }


def regenerate(phone: str, *, force: bool = False) -> dict:
    """Rebuild HTML from current caller memory if opted in. Never raises."""
    key = _normalize_phone(phone)
    if not key:
        return {"ok": False, "regenerated": False, "error": "invalid or missing phone"}
    try:
        flags = _consent_flags(key)
        if not flags.get("memory_ok"):
            return {
                "ok": False,
                "regenerated": False,
                "needs_memory_ok": True,
                "error": "memory_ok required before generating a personal page",
            }
        if not flags.get("personal_page_ok"):
            return {
                "ok": False,
                "regenerated": False,
                "needs_personal_page_ok": True,
                "error": "personal_page_ok opt-in required",
            }
        prof = _raw_profile(key)
        if not prof:
            return {
                "ok": False,
                "regenerated": False,
                "error": "no caller profile yet — chat with AI 411 first",
            }
        with _lock:
            pages = _load_registry()
            meta = copy.deepcopy(pages.get(key) or {})
            if not meta.get("enabled") and not force:
                # allow regenerate when force after opt-in path sets enabled
                if not meta.get("slug"):
                    return {
                        "ok": False,
                        "regenerated": False,
                        "error": "page not enabled",
                    }
            if not meta.get("slug"):
                meta["slug"] = _new_slug()
            meta["enabled"] = True
            meta["phone_e164"] = key
            meta["last_generated_at"] = _now_iso()
            meta["updated_at"] = meta["last_generated_at"]
            payload = _public_payload(prof, meta)
            html_doc = _render_html(payload)
            _write_html(meta["slug"], html_doc)
            pages[key] = meta
            # secondary index slug → phone for public GET
            index = pages.setdefault("_slug_index", {})
            if not isinstance(index, dict):
                index = {}
                pages["_slug_index"] = index
            # drop old slug mappings for this phone
            for s, p in list(index.items()):
                if p == key and s != meta["slug"]:
                    del index[s]
            index[meta["slug"]] = key
            _save_registry(pages)
        return {
            "ok": True,
            "regenerated": True,
            "url": _public_url(meta["slug"]),
            "slug": meta["slug"],
            "last_generated_at": meta["last_generated_at"],
            "next_regen_at": (
                _now() + timedelta(hours=_ttl_hours())
            ).isoformat(),
            "message": f"Page refreshed. Public link: {_public_url(meta['slug'])}",
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "regenerated": False,
            "error": f"data unavailable ({e.__class__.__name__})",
        }


def ensure_fresh(phone: str) -> dict:
    """Regenerate if missing or older than TTL."""
    key = _normalize_phone(phone)
    if not key:
        return {"ok": False, "error": "invalid or missing phone"}
    status = get_personal_page_status(key)
    if not status.get("enabled"):
        return status
    if status.get("stale") or not status.get("slug"):
        return regenerate(key, force=True)
    return {
        **status,
        "regenerated": False,
        "message": status.get("message") or "Page is fresh.",
    }


def opt_in_personal_page(
    phone: str,
    *,
    preferred_name: str = "",
    display_name: str = "",
    headline: str = "",
    source: str = "voice",
) -> dict:
    """Enable personal page + memory; generate immediately. Never raises."""
    key = _normalize_phone(phone)
    if not key:
        return {"ok": False, "enabled": False, "error": "invalid or missing phone"}
    try:
        patch: dict[str, Any] = {
            "consent": {"memory_ok": True, "personal_page_ok": True},
            "preferences": {"personal_page": True},
        }
        if preferred_name.strip():
            patch["preferred_name"] = preferred_name.strip()[:80]
        if display_name.strip():
            patch["display_name"] = display_name.strip()[:120]
        up = callers.update_profile(key, patch)
        if not up.get("updated"):
            return {
                "ok": False,
                "enabled": False,
                "error": up.get("error") or "could not update profile",
            }
        with _lock:
            pages = _load_registry()
            meta = copy.deepcopy(pages.get(key) or {})
            meta["enabled"] = True
            meta["phone_e164"] = key
            meta["source"] = (source or "voice")[:64]
            meta["opted_in_at"] = meta.get("opted_in_at") or _now_iso()
            meta["updated_at"] = _now_iso()
            if headline.strip():
                meta["headline"] = headline.strip()[:160]
            if not meta.get("slug"):
                meta["slug"] = _new_slug()
            pages[key] = meta
            index = pages.setdefault("_slug_index", {})
            if not isinstance(index, dict):
                index = {}
                pages["_slug_index"] = index
            index[meta["slug"]] = key
            _save_registry(pages)
        gen = regenerate(key, force=True)
        if not gen.get("ok"):
            return {
                "ok": False,
                "enabled": True,
                "slug": meta.get("slug"),
                "error": gen.get("error") or "generate failed",
                "message": "Opt-in saved but page generation failed — try again shortly.",
            }
        return {
            "ok": True,
            "enabled": True,
            "url": gen.get("url"),
            "slug": gen.get("slug"),
            "last_generated_at": gen.get("last_generated_at"),
            "next_regen_at": gen.get("next_regen_at"),
            "message": (
                "You're opted in. Free personal page is live and will rebuild about "
                f"every {_ttl_hours()} hours from what AI 411 remembers. "
                f"Link: {gen.get('url')}"
            ),
            "speakable": (
                f"You're opted in. Your free personal page is live and refreshes about "
                f"every {_ttl_hours()} hours. I can text you the link if you want."
            ),
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "enabled": False,
            "error": f"data unavailable ({e.__class__.__name__})",
        }


def opt_out_personal_page(phone: str) -> dict:
    """Disable page, drop HTML, clear personal_page_ok. Never raises."""
    key = _normalize_phone(phone)
    if not key:
        return {"ok": False, "enabled": False, "error": "invalid or missing phone"}
    try:
        # Only patch consent if a profile already exists — never recreate after forget.
        existing = _raw_profile(key)
        if existing is not None:
            callers.update_profile(
                key,
                {
                    "consent": {"personal_page_ok": False},
                    "preferences": {"personal_page": False},
                },
            )
        clear_for_phone(key)
        return {
            "ok": True,
            "enabled": False,
            "forgotten_page": True,
            "message": "Personal page removed. Memory preferences are unchanged unless you ask to forget everything.",
            "speakable": "Done — your personal page is down.",
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"data unavailable ({e.__class__.__name__})",
        }


def clear_for_phone(phone: str) -> None:
    """Wipe registry + HTML only (no caller profile writes). Used by forget_caller."""
    key = _normalize_phone(phone)
    if not key:
        return
    try:
        slug = ""
        with _lock:
            pages = _load_registry()
            meta = pages.pop(key, None)
            if isinstance(meta, dict):
                slug = str(meta.get("slug") or "")
            index = pages.get("_slug_index")
            if isinstance(index, dict):
                if slug:
                    index.pop(slug, None)
                for s, p in list(index.items()):
                    if p == key:
                        del index[s]
            _save_registry(pages)
        if slug:
            _delete_html(slug)
    except Exception:
        pass


def render_public(slug: str) -> str | None:
    """Return HTML for a public slug, regenerating if stale. None if missing."""
    if not slug or not re.fullmatch(r"p-[a-f0-9]{16}", slug.strip()):
        return None
    slug = slug.strip()
    try:
        with _lock:
            pages = _load_registry()
            _raw_index = pages.get("_slug_index")
            index: dict[str, Any] = _raw_index if isinstance(_raw_index, dict) else {}
            phone = index.get(slug)
            if not phone:
                # linear fallback
                for p, meta in pages.items():
                    if p.startswith("_"):
                        continue
                    if isinstance(meta, dict) and meta.get("slug") == slug and meta.get("enabled"):
                        phone = p
                        break
            meta = pages.get(phone) if phone else None
        if not phone or not meta or not meta.get("enabled"):
            return None
        flags = _consent_flags(phone)
        if not flags.get("memory_ok") or not flags.get("personal_page_ok"):
            return None
        # Stale → regenerate
        status = _status_from_meta(phone, meta, flags=flags)
        if status.get("stale"):
            regenerate(phone, force=True)
        path = _pages_dir() / f"{slug}.html"
        if path.exists():
            return path.read_text(encoding="utf-8")
        # Missing file — rebuild
        gen = regenerate(phone, force=True)
        if gen.get("ok") and path.exists():
            return path.read_text(encoding="utf-8")
        return None
    except Exception:
        return None


def list_enabled_phones() -> list[str]:
    """Phones with enabled pages (for cron regen)."""
    try:
        with _lock:
            pages = _load_registry()
        out = []
        for p, meta in pages.items():
            if p.startswith("_"):
                continue
            if isinstance(meta, dict) and meta.get("enabled") and meta.get("slug"):
                out.append(p)
        return out
    except Exception:
        return []


def regen_all_stale() -> dict:
    """Batch ensure_fresh for cron. Never raises."""
    ok = 0
    skipped = 0
    errors = 0
    details = []
    for phone in list_enabled_phones():
        try:
            st = get_personal_page_status(phone)
            if not st.get("stale"):
                skipped += 1
                continue
            r = regenerate(phone, force=True)
            if r.get("ok"):
                ok += 1
            else:
                errors += 1
            details.append({"phone_tail": phone[-4:], "ok": r.get("ok"), "error": r.get("error")})
        except Exception as e:  # noqa: BLE001
            errors += 1
            details.append({"error": e.__class__.__name__})
    return {
        "ok": True,
        "regenerated": ok,
        "skipped_fresh": skipped,
        "errors": errors,
        "details": details[:50],
    }
